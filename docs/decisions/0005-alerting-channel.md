# ADR-0005: Alerting Channel to Mike

**Status:** Accepted
**Date:** 2026-05-29
**Deciders:** Mike White

## Context

The Reporting Department produces three classes of output that need to reach
Mike: routine daily briefings, weekly reviews, and urgent alerts (risk breach,
system down, reconciliation discrepancy). The architecture's Open Question 2
has been deferring the channel choice; the interim has been "log to PostgreSQL,
surface in daily briefing," which is fine for routine content but inadequate
for anything urgent.

Two distinct delivery requirements:

1. **Routine** — daily briefing, weekly review, non-time-sensitive notifications.
   Latency target: hours. Mike reads on his own schedule. Channel must support
   readable formatted content and be persistent (Mike can scroll back).

2. **Urgent** — risk breach, system down, reconciliation discrepancy,
   credential incident. Latency target: minutes, ideally seconds. Must reach
   Mike when he is away from his desk, including on his phone, without
   depending on Mike checking a tab.

Sovereignty constraint per the vision: minimize dependencies on services Mike
does not control. SaaS messaging is acceptable for routine content where the
worst case is "Mike reads the briefing 30 minutes later than he could have."
For urgent alerts, the worst case is a missed risk event — the channel
needs to actually fire.

Mike already runs a Discord-based stack for the legacy Shrap tooling. That
context is real and reduces friction: Mike is already on Discord, the
notification habits are already there, and the webhook pattern is well-trodden
for ops alerts.

## Decision

Two-channel routing, classified by urgency at publish time.

**Routine → Discord webhook.** Daily briefings, weekly reviews,
non-time-sensitive notifications (`strategy.promoted`, `strategy.retired`,
`deployment.completed` summaries) post to a dedicated Discord channel via
webhook. Matches the legacy Shrap stack; no new tools for Mike to learn or
ignore. Free, requires no self-hosting. Discord outage during the sprint is
acceptable — the content is also written to `docs/reports/` in the repo as
the durable record.

**Urgent → ntfy.sh self-hosted, with Pushover as a documented fallback.**
Urgent alerts route to a self-hosted `ntfy.sh` instance running on the Dell.
ntfy.sh delivers push notifications to Mike's phone via the ntfy mobile app,
runs in a single Docker container, and is fully sovereign — no third-party
service in the critical path. The Alert Agent publishes to a topic protected
by an auth token; the phone app subscribes to the same topic over Tailscale
or a public ntfy.sh endpoint if Tailscale is unavailable.

Pushover is a documented fallback because it is paid-once, reliable, and would
be the immediate substitute if self-hosted ntfy proves unreliable in practice.
The fallback is configured but not active; switching is a one-line change in
the Alert Agent's channel config.

**Classification rules.** The Alert Agent receives all candidate events from
Redis Streams and classifies before routing:

- `risk.breach`, `risk.veto` (when triggered against a live order),
  `health.anomaly` with severity=critical, `reconciliation.discrepancy`,
  credential incidents → urgent → ntfy
- `report.daily.generated`, `report.weekly.generated`,
  `strategy.promoted`, `strategy.retired`, `deployment.completed`,
  `health.anomaly` with severity=warning → routine → Discord
- Everything else → log to PostgreSQL, surface in next daily briefing

The classification rules live in the Alert Agent spec, version-controlled,
and reviewable. Mike approves any change to what counts as urgent.

## Alternatives Considered

**Slack.** Low friction, good formatting, mobile push works well. Eliminated:
third-party dependency Mike does not currently use, vendor lock-in for a tool
that is not core to the firm. Discord covers the same need and Mike is already
there.

**Email.** Reliable, durable, archival. Eliminated for urgent alerts:
notification latency is unpredictable (phone email apps batch poll), and
email pushes are easy to miss in a busy inbox. Acceptable as a tertiary
backup but not the primary urgent channel. Daily briefing could be emailed in
addition to Discord — deferred as a "nice to have" post-sprint.

**Self-hosted web dashboard.** Sovereign, formatable, persistent. Eliminated
for urgent alerts: requires Mike to be looking at the dashboard, which
defeats the point. Useful as a supplementary inspection surface; Grafana
(ADR-0004) covers most of that ground already.

**Pushover as primary.** Reliable paid service, designed for ops push
notifications. Eliminated as primary: small third-party dependency where a
self-hosted equivalent (ntfy.sh) is straightforwardly available. Kept as
fallback because the urgent channel is the one Mike actually needs to work,
and falling back to a paid reliable service is better than degrading silently
if self-hosted ntfy has issues.

**SMS.** Highest reliability for getting a phone to buzz. Eliminated: requires
Twilio or equivalent, costs per message, adds a vendor for one feature.
Reconsider only if ntfy and Pushover both prove insufficient in practice.

**No urgent channel during the sprint (paper trading only).** Tempting:
nothing has real money at risk, so a delayed alert is an inconvenience, not
a loss. Eliminated because the system needs to behave correctly under real
conditions before it is trusted with real conditions. The sprint is the
test; building muscle for "alert fires within 30 seconds" matters even when
the underlying event is paper.

## Consequences

**Enables:** Reporting Department spec can be written. The Operations
Department's `health.anomaly` events have a documented destination. Mike
gets a phone-buzz path for things that matter and a scroll-back history for
things that do not.

**Constrains:** Two more containers on the Dell (ntfy.sh server, and the
Alert Agent if it is not already deployed) and a small phone-app dependency.
Mike must install ntfy on his phone and subscribe to the topic. If Mike's
phone is off, ntfy queues notifications and delivers on next connection —
adequate for the sprint but not a substitute for someone actually watching
the system.

**Cost:** Discord — free. Self-hosted ntfy.sh — free, one container. Pushover
fallback — $5 one-time per platform if activated. Negligible.

**Sovereignty consequence:** The urgent path is fully self-hosted at steady
state. Discord remains the routine path because the cost of routine content
being delayed by a Discord outage is small and well-understood. If Discord
becomes hostile in any vendor-specific way, swapping the routine channel to
Matrix, Rocket.Chat, or a self-hosted ntfy topic is straightforward — the
Alert Agent treats routing destinations as configuration, not code.

## Notes

The two-channel split exists because trying to use one channel for both
needs is the trap: a channel calibrated for urgent fires too often for
routine, and a channel calibrated for routine misses urgent. Splitting them
makes each channel's noise-floor match Mike's tolerance for it.

The classification rules will need calibration in the first month. Expected
mistakes: over-classifying things as urgent (alert fatigue) and
under-classifying things as routine (Mike misses something). The Alert Agent
records every classification decision to PostgreSQL for retrospective
review.
