-- Firm health: is Shrap actually running, and is it running the way it was measured?
--
-- Read-only. One table, one row per finding, worst first, then a SUMMARY row.
-- Built to be read by an overseer agent (Hermes) or by Mike, so every row says
-- what was checked, what it found, what the threshold is, and why it matters.
--
--   make firm-health                    # from the repo root on the Dell
--   scripts/firm-health.sh              # same thing
--
-- Why it exists: in late October the daily_loss switch latched and the firm
-- refused every new buy for over a week. Each component was healthy by its own
-- measure and nothing compared them. These are the cross-component questions
-- that would have caught it on the first morning.
--
-- status:  ALERT  act today       WARN  look this week       OK  nothing to do
--
-- Thresholds are first cuts, not rulings. Override any of them with -v, e.g.
--   -v stale_fill_days=5 -v shadow_gap_pts=5

\set QUIET on
\set ON_ERROR_STOP on
\pset footer off
\pset null '-'

\if :{?window_days}       \else \set window_days 30       \endif
\if :{?stale_fill_days}   \else \set stale_fill_days 4    \endif
\if :{?regime_flips_max}  \else \set regime_flips_max 4   \endif
\if :{?exposure_floor}    \else \set exposure_floor 0.5   \endif
\if :{?shadow_gap_pts}    \else \set shadow_gap_pts 3     \endif
\if :{?paper_stage}       \else \set paper_stage 0.80     \endif

-- Accounts the firm is running: every account with a strategy assigned.
CREATE TEMP VIEW accts AS
SELECT account_id, strategy_id, name AS strategy
FROM research.strategies
WHERE account_id IS NOT NULL AND status IN ('paper', 'small-size-paper', 'live-paper');

CREATE TEMP VIEW regime_now AS
SELECT label, band_lo, confidence, at
FROM intel.regime_history
ORDER BY at DESC
LIMIT 1;

CREATE TEMP VIEW findings AS

-- 1. Kill switches still active from an earlier session -------------------------
SELECT CASE WHEN s.switch = 'daily_loss' THEN 'ALERT' ELSE 'WARN' END AS status,
       'switch-latched' AS check_name,
       s.switch AS subject,
       'active since ' || to_char(s.at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || 'Z by ' || s.actor AS found,
       'clears at the session boundary (daily_loss) or by ruling' AS threshold,
       CASE WHEN s.switch = 'daily_loss'
            THEN 'firm-wide halt on new buys; the session clear did not run. Reason: ' || s.reason
            ELSE 'halt waiting on a human ruling. Reason: ' || s.reason END AS why
FROM (SELECT DISTINCT ON (switch) switch, active, actor, reason, at
      FROM risk.kill_switches ORDER BY switch, id DESC) s
WHERE s.active AND (s.at AT TIME ZONE 'UTC')::date < (now() AT TIME ZONE 'UTC')::date

UNION ALL

-- 2. Buys refused versus approved, last 24 hours ---------------------------------
SELECT CASE WHEN d.approved_buys = 0 AND d.refused_buys > 0 THEN 'ALERT'
            WHEN d.refused_buys > d.approved_buys THEN 'WARN' ELSE 'OK' END,
       'buys-refused-24h',
       d.account_id,
       d.approved_buys || ' approved, ' || d.refused_buys || ' refused'
         || coalesce(' (top reason ' || d.top_reason || ')', ''),
       'refused <= approved',
       'an account that can only sell drains to cash and stops testing its strategy'
FROM (SELECT a.account_id,
             count(*) FILTER (WHERE r.approved) AS approved_buys,
             count(*) FILTER (WHERE NOT r.approved) AS refused_buys,
             mode() WITHIN GROUP (ORDER BY r.reason_code) FILTER (WHERE NOT r.approved) AS top_reason
      FROM accts a
      LEFT JOIN risk.decisions r
        ON r.account_id = a.account_id
       AND r.at >= now() - interval '24 hours'
       AND lower(r.side) LIKE '%buy%'
      GROUP BY a.account_id) d
WHERE d.approved_buys + d.refused_buys > 0

UNION ALL

-- 3. Exposure versus what the stage fraction and regime say it should be -------
SELECT CASE WHEN x.invested < :exposure_floor * x.target THEN 'WARN' ELSE 'OK' END,
       'exposure-vs-target',
       x.account_id,
       round(100 * x.invested::numeric, 1) || '% invested',
       '>= ' || round(100 * :exposure_floor * x.target::numeric, 1) || '% (half of target '
         || round(100 * x.target::numeric, 1) || '% = stage ' || :paper_stage
         || ' x regime ' || x.band_lo || ')',
       'a book far below target is not running the strategy that was measured at full exposure'
FROM (SELECT a.account_id,
             greatest(0, 1 - s.cash / nullif(s.equity, 0)) AS invested,
             :paper_stage * rn.band_lo AS target,
             rn.band_lo
      FROM accts a
      CROSS JOIN regime_now rn
      JOIN LATERAL (SELECT cash, equity FROM ops.account_snapshots
                    WHERE account_id = a.account_id AND equity > 0
                    ORDER BY at DESC LIMIT 1) s ON true) x

UNION ALL

-- 4. Days since each account last filled anything --------------------------------
SELECT CASE WHEN f.days IS NULL OR f.days > 2 * :stale_fill_days THEN 'ALERT'
            WHEN f.days > :stale_fill_days THEN 'WARN' ELSE 'OK' END,
       'days-since-fill',
       a.account_id,
       coalesce(f.days || ' days (last ' || f.last_fill || ')', 'never filled'),
       '<= ' || :stale_fill_days || ' calendar days',
       'a daily strategy that stops filling has stopped, whatever its container says'
FROM accts a
LEFT JOIN (SELECT account_id,
                  max(occurred_at)::date AS last_fill,
                  current_date - max(occurred_at)::date AS days
           FROM trading.paper_order_events
           WHERE event_topic = 'execution.order.filled'
           GROUP BY account_id) f USING (account_id)

UNION ALL

-- 5. Regime label flips in the last 24 hours -------------------------------------
SELECT CASE WHEN r.flips > :regime_flips_max THEN 'WARN' ELSE 'OK' END,
       'regime-flips-24h',
       'classifier',
       r.flips || ' flips; now ' || rn.label || ' at confidence ' || round(rn.confidence::numeric, 2),
       '<= ' || :regime_flips_max || ' per day',
       'every flip moves every exposure cap; flapping whipsaws sizing with no change in the market'
FROM (SELECT count(*) FILTER (WHERE label IS DISTINCT FROM prev) AS flips
      FROM (SELECT label, lag(label) OVER (ORDER BY at) AS prev
            FROM intel.regime_history
            WHERE at >= now() - interval '24 hours') t
      WHERE prev IS NOT NULL) r
CROSS JOIN regime_now rn

UNION ALL

-- 6. Live account versus its own shadow record -----------------------------------
-- The shadow ledger forward-tests the signal at full exposure with close fills.
-- Scaling it by the account's average exposure gives what the account should
-- have earned if execution matched the measurement. The gap is everything the
-- firm does between signal and fill: sizing, refusals, caps, timing, costs.
SELECT CASE WHEN g.gap_pts < -:shadow_gap_pts THEN 'WARN' ELSE 'OK' END,
       'live-vs-shadow',
       g.account_id || ' / ' || g.strategy,
       'live ' || round(g.live_pts::numeric, 2) || ' pts vs shadow-at-exposure '
         || round(g.shadow_scaled_pts::numeric, 2) || ' pts, gap '
         || round(g.gap_pts::numeric, 2) || ' over ' || g.sessions || ' sessions',
       'gap >= -' || :shadow_gap_pts || ' pts',
       'the signal and the account disagree; execution is losing what the strategy finds'
FROM (SELECT a.account_id, a.strategy,
             100 * (e.end_eq / e.start_eq - 1) AS live_pts,
             100 * (exp(sh.log_net) - 1) * e.avg_invested AS shadow_scaled_pts,
             100 * (e.end_eq / e.start_eq - 1) - 100 * (exp(sh.log_net) - 1) * e.avg_invested AS gap_pts,
             sh.sessions
      FROM accts a
      JOIN LATERAL (
        SELECT (array_agg(equity ORDER BY at))[1] AS start_eq,
               (array_agg(equity ORDER BY at DESC))[1] AS end_eq,
               avg(greatest(0, 1 - cash / equity)) AS avg_invested
        FROM ops.account_snapshots
        WHERE account_id = a.account_id AND equity > 0
          AND at >= now() - (:window_days || ' days')::interval) e ON e.start_eq > 0
      JOIN LATERAL (
        SELECT sum(ln(1 + net_return)) AS log_net, count(*) AS sessions
        FROM research.shadow_ledger
        WHERE strategy_id = a.strategy_id AND settled_at IS NOT NULL AND net_return > -1
          AND session_date >= current_date - :window_days) sh ON sh.sessions > 0) g

UNION ALL

-- 7. Are the inputs arriving? -----------------------------------------------------
SELECT CASE WHEN age > interval '4 days' THEN 'ALERT'
            WHEN age > interval '2 days' THEN 'WARN' ELSE 'OK' END,
       'input-freshness',
       src,
       'newest ' || to_char(newest, 'YYYY-MM-DD HH24:MI'),
       'within 2 days',
       'stale inputs mean every downstream decision is made on old facts'
FROM (SELECT 'daily_bars (alpaca-iex)' AS src, max(session_date)::timestamptz AS newest,
             now() - max(session_date)::timestamptz - interval '1 day' AS age
      FROM market_data.daily_bars WHERE source = 'alpaca-iex'
      UNION ALL
      SELECT 'account_snapshots', max(at), now() - max(at) FROM ops.account_snapshots
      UNION ALL
      SELECT 'regime_history', max(at), now() - max(at) FROM intel.regime_history
      UNION ALL
      SELECT 'shadow_ledger (settled)', max(settled_at), now() - max(settled_at)
      FROM research.shadow_ledger) i;

\echo
\echo 'Shrap firm health'
SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || 'Z' AS checked_at,
       (SELECT count(*) FROM accts) AS accounts,
       (SELECT label || ' (' || round(confidence::numeric, 2) || ')' FROM regime_now) AS regime;

SELECT status, check_name, subject, found, threshold, why
FROM findings
ORDER BY CASE status WHEN 'ALERT' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END, check_name, subject;

SELECT 'SUMMARY' AS status,
       count(*) FILTER (WHERE status = 'ALERT') AS alerts,
       count(*) FILTER (WHERE status = 'WARN') AS warnings,
       count(*) FILTER (WHERE status = 'OK') AS ok
FROM findings;
