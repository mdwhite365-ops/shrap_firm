"""Strategies as specs: a small signal language, so a new idea is data, not a PR.

Until this module every new kind of strategy was a new rule class and a PR —
five rules, five factors, in two months. The literature funnel's blocked list
(`research.literature_items`, outcome ``capability-gap``) shows what that cost:
``magnitude-shrinkage`` is "the absolute value of yesterday's return, ranked",
and it sat unbuilt because building it meant writing a class.

A ``signal-spec`` strategy is a JSON expression that scores every name, plus a
rule for turning scores into a book::

    {"rule": "signal-spec",
     "params": {
       "signal": {"op": "sub", "args": [
           {"op": "rank", "of": {"feature": "return", "lookback": 126, "skip": 21}},
           {"op": "rank", "of": {"feature": "volatility", "lookback": 60}}]},
       "select": "top", "top_n": 10, "gross_exposure": 1.0,
       "weighting": "equal", "rebalance": "monthly"},
     "param_bounds": {"top_n": [1, 50], "gross_exposure": [0, 1]}}

**Features** are per-name and read only their own trailing bars, so a backtest
over 1,500 bars stays linear. **Operators** either combine a name's values
(``add``, ``sub``, ``mul``, ``div``, ``neg``, ``abs``, ``log``) or compare names
(``rank``, ``zscore``). **Selection** is ``top`` / ``bottom`` N, ``positive``
(every name scoring above zero, an absolute rule), or ``long_short``.

Deliberately small. Every feature here is computable from the panel the firm
already holds (close, volume, market cap); nothing is named for data the firm
does not have, because a feature that silently returned nothing would make a
strategy look flat rather than impossible. The tree is bounded in depth and size
so a machine-written spec cannot become an unreadable search over formulas.

**What this cannot express, on purpose:** anything path-dependent across
rebalances (a stop, a trailing high since entry) and anything needing another
name's history inside a per-name feature. Those are exit rules and relational
factors, which have their own homes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Any

from shrap.market_data.fundamentals import METRICS
from shrap.research.strategy_evaluator.cross_sectional import (
    _equal_weights,
    _inverse_volatility_weights,
    _long_short_weights,
    _trailing_vol,
)
from shrap.research.strategy_evaluator.strategy import PanelWindow

SIGNAL_SPEC_NAME = "signal-spec"

MAX_DEPTH = 6
MAX_NODES = 40
MIN_LOOKBACK = 1
MAX_LOOKBACK = 756  # three years of sessions

SELECT_TOP = "top"
SELECT_BOTTOM = "bottom"
SELECT_POSITIVE = "positive"
SELECT_LONG_SHORT = "long_short"
SELECTIONS = frozenset({SELECT_TOP, SELECT_BOTTOM, SELECT_POSITIVE, SELECT_LONG_SHORT})

WEIGHTING_EQUAL = "equal"
WEIGHTING_INVERSE_VOL = "inverse-vol"
WEIGHTINGS = frozenset({WEIGHTING_EQUAL, WEIGHTING_INVERSE_VOL})
INVERSE_VOL_WINDOW = 21

REBALANCE_DAILY = "daily"
REBALANCE_WEEKLY = "weekly"
REBALANCE_MONTHLY = "monthly"
REBALANCES = frozenset({REBALANCE_DAILY, REBALANCE_WEEKLY, REBALANCE_MONTHLY})
# Extra bars of warmup a scheduled rebalance needs: it may act on a window up
# to one period old, so the signal must be computable that far back.
_REBALANCE_SLACK = {REBALANCE_DAILY: 0, REBALANCE_WEEKLY: 5, REBALANCE_MONTHLY: 23}

PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "top_n": (1.0, 50.0),
    "gross_exposure": (0.0, 1.0),
}


class SignalSpecError(ValueError):
    """The expression or its book rule is malformed. Refused before any run."""


Scores = dict[str, float | None]


# --- features: one name, its own trailing bars ----------------------------------


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _tail(series: tuple[float, ...], n: int) -> tuple[float, ...] | None:
    """The last ``n`` values, or ``None`` if the name has fewer, or any is unusable."""

    if len(series) < n:
        return None
    out = series[-n:]
    if any(not math.isfinite(v) for v in out):
        return None
    return out


def _f_return(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    lookback, skip = a["lookback"], a.get("skip", 0)
    closes = _tail(w.closes(t), lookback + skip + 1)
    if closes is None or closes[0] <= 0.0:
        return None
    return closes[-1 - skip] / closes[0] - 1.0


def _f_abs_return(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    value = _f_return(w, t, a)
    return None if value is None else abs(value)


def _f_volatility(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    return _trailing_vol(w, t, a["lookback"])


def _f_volume_ratio(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    lookback, recent = a["lookback"], a.get("recent", 5)
    vols = _tail(w.volumes(t), lookback)
    if vols is None:
        return None
    base = _mean(vols)
    return None if base <= 0.0 else _mean(vols[-recent:]) / base


def _f_high_proximity(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    closes = _tail(w.closes(t), a["lookback"])
    if closes is None:
        return None
    high = max(closes)
    return None if high <= 0.0 else closes[-1] / high


def _f_sma_ratio(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    closes = _tail(w.closes(t), a["slow"])
    if closes is None:
        return None
    slow = _mean(closes)
    return None if slow <= 0.0 else _mean(closes[-a["fast"] :]) / slow - 1.0


def _f_bollinger_z(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    closes = _tail(w.closes(t), a["lookback"])
    if closes is None:
        return None
    sd = _stdev(closes)
    return None if not sd else (closes[-1] - _mean(closes)) / sd


def _f_rsi(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    """Simple-average RSI (Cutler's form) over the last ``lookback`` changes.

    Wilder's smoothing carries state from the first bar ever seen, so its value
    depends on where the panel starts; the simple form depends only on the
    window, which keeps a backtest and the live Runner (whose panels start on
    different dates) computing the same number.
    """

    closes = _tail(w.closes(t), a["lookback"] + 1)
    if closes is None:
        return None
    gains = losses = 0.0
    for prev, cur in pairwise(closes):
        change = cur - prev
        if change > 0:
            gains += change
        else:
            losses -= change
    if gains + losses == 0.0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def _ema(values: Sequence[float], span: int) -> list[float]:
    k = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _f_macd(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    """MACD histogram over price: (MACD line - signal line) / close.

    EMAs are seeded from a fixed trailing window of ``3 x slow + signal`` bars
    rather than from the first bar in the panel, for the same reason RSI is:
    so the value depends on the window, not on where history happens to begin.
    Divided by price so names are comparable across a ranking.
    """

    fast, slow, sig = a["fast"], a["slow"], a["signal"]
    closes = _tail(w.closes(t), 3 * slow + sig)
    if closes is None or closes[-1] <= 0.0:
        return None
    line = [f - s for f, s in zip(_ema(closes, fast), _ema(closes, slow), strict=True)]
    signal_line = _ema(line, sig)
    return (line[-1] - signal_line[-1]) / closes[-1]


def _f_donchian(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    closes = _tail(w.closes(t), a["lookback"])
    if closes is None:
        return None
    lo, hi = min(closes), max(closes)
    return None if hi == lo else (closes[-1] - lo) / (hi - lo)


def _f_market_cap(w: PanelWindow, t: str, a: Mapping[str, int]) -> float | None:
    caps = w.market_caps(t)
    if not caps or not math.isfinite(caps[-1]) or caps[-1] <= 0.0:
        return None
    return caps[-1]


@dataclass(frozen=True, slots=True)
class _Feature:
    compute: Callable[[PanelWindow, str, Mapping[str, int]], float | None]
    required: tuple[str, ...]
    optional: Mapping[str, int]
    bars: Callable[[Mapping[str, int]], int]
    """How many trailing bars the feature needs, from its arguments."""


FEATURES: dict[str, _Feature] = {
    "return": _Feature(
        _f_return, ("lookback",), {"skip": 0}, lambda a: a["lookback"] + a["skip"] + 1
    ),
    "abs_return": _Feature(
        _f_abs_return, (), {"lookback": 1, "skip": 0}, lambda a: a["lookback"] + a["skip"] + 1
    ),
    "volatility": _Feature(_f_volatility, ("lookback",), {}, lambda a: a["lookback"] + 1),
    "volume_ratio": _Feature(
        _f_volume_ratio, ("lookback",), {"recent": 5}, lambda a: a["lookback"]
    ),
    "high_proximity": _Feature(_f_high_proximity, ("lookback",), {}, lambda a: a["lookback"]),
    "sma_ratio": _Feature(_f_sma_ratio, ("fast", "slow"), {}, lambda a: a["slow"]),
    "bollinger_z": _Feature(_f_bollinger_z, ("lookback",), {}, lambda a: a["lookback"]),
    "rsi": _Feature(_f_rsi, (), {"lookback": 14}, lambda a: a["lookback"] + 1),
    "macd": _Feature(
        _f_macd,
        (),
        {"fast": 12, "slow": 26, "signal": 9},
        lambda a: 3 * a["slow"] + a["signal"],
    ),
    "donchian": _Feature(_f_donchian, ("lookback",), {}, lambda a: a["lookback"]),
    "market_cap": _Feature(_f_market_cap, (), {}, lambda a: 1),
}


def _a_fundamental(w: PanelWindow, t: str, metric: str) -> float | None:
    return w.fundamental(t, metric)


def _a_fundamental_growth(w: PanelWindow, t: str, metric: str) -> float | None:
    """Year-on-year growth of a filed figure, both years as visible on this bar."""

    now, before = w.fundamental(t, metric), w.fundamental_prior(t, metric)
    if now is None or before is None or before <= 0.0:
        return None
    return now / before - 1.0


# Features over filed accounting figures (``market_data.fundamentals``), point in
# time on the filing date. They take a metric name rather than a lookback.
# Ratios such as earnings-to-price are built with ``div`` against ``market_cap``.
ACCOUNTING_FEATURES: dict[str, Callable[[PanelWindow, str, str], float | None]] = {
    "fundamental": _a_fundamental,
    "fundamental_growth": _a_fundamental_growth,
}


# --- the expression tree --------------------------------------------------------


UNARY_OPS = frozenset({"neg", "abs", "log", "rank", "zscore"})
NARY_OPS = frozenset({"add", "sub", "mul", "div"})


@dataclass(frozen=True, slots=True)
class Node:
    """One validated node. Build with :func:`parse`, never by hand."""

    kind: str  # "feature" | "op" | "const"
    name: str
    args: Mapping[str, int]
    children: tuple[Node, ...]
    value: float = 0.0
    metric: str = ""
    """For the accounting features: which filed figure (see ``market_data.fundamentals``)."""

    @property
    def bars(self) -> int:
        """Trailing bars the whole subtree needs."""

        if self.kind == "feature":
            return FEATURES[self.name].bars(self.args) if self.name in FEATURES else 1
        return max((c.bars for c in self.children), default=1)

    def describe(self) -> str:
        if self.kind == "const":
            return f"{self.value:g}"
        if self.kind == "feature":
            if self.metric:
                return f"{self.name}({self.metric})"
            inner = ", ".join(f"{k}={v}" for k, v in sorted(self.args.items()))
            return f"{self.name}({inner})"
        if self.name in UNARY_OPS:
            return f"{self.name}({self.children[0].describe()})"
        symbol = {"add": " + ", "sub": " - ", "mul": " * ", "div": " / "}[self.name]
        return "(" + symbol.join(c.describe() for c in self.children) + ")"


def _int_arg(name: str, key: str, raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != int(raw):
        raise SignalSpecError(f"{name}.{key} must be a whole number of bars, got {raw!r}")
    value = int(raw)
    lo = 0 if key == "skip" else MIN_LOOKBACK
    if not lo <= value <= MAX_LOOKBACK:
        raise SignalSpecError(f"{name}.{key}={value} is outside [{lo}, {MAX_LOOKBACK}]")
    return value


def parse(raw: object, *, _depth: int = 1, _count: list[int] | None = None) -> Node:
    """Validate an expression and return its tree, or raise :class:`SignalSpecError`."""

    count = _count if _count is not None else [0]
    count[0] += 1
    if count[0] > MAX_NODES:
        raise SignalSpecError(f"expression has more than {MAX_NODES} nodes")
    if _depth > MAX_DEPTH:
        raise SignalSpecError(f"expression is deeper than {MAX_DEPTH} levels")
    if not isinstance(raw, Mapping):
        raise SignalSpecError(f"expected an expression object, got {raw!r}")

    if "const" in raw:
        value = raw["const"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SignalSpecError(f"const must be a number, got {value!r}")
        if not math.isfinite(float(value)):
            raise SignalSpecError("const must be finite")
        return Node("const", "const", {}, (), float(value))

    if "feature" in raw and raw["feature"] in ACCOUNTING_FEATURES:
        name = str(raw["feature"])
        if set(raw) != {"feature", "metric"}:
            raise SignalSpecError(f"{name!r} takes exactly one argument, 'metric'")
        metric = str(raw["metric"])
        if metric not in METRICS:
            known = ", ".join(sorted(METRICS))
            raise SignalSpecError(f"unknown metric {metric!r}; known metrics are {known}")
        return Node("feature", name, {}, (), metric=metric)

    if "feature" in raw:
        name = raw["feature"]
        feature = FEATURES.get(str(name))
        if feature is None:
            known = ", ".join(sorted(FEATURES))
            raise SignalSpecError(f"unknown feature {name!r}; known features are {known}")
        unknown = set(raw) - {"feature", *feature.required, *feature.optional}
        if unknown:
            raise SignalSpecError(f"feature {name!r} takes no argument(s) {sorted(unknown)}")
        args: dict[str, int] = {}
        for key in feature.required:
            if key not in raw:
                raise SignalSpecError(f"feature {name!r} requires {key!r}")
            args[key] = _int_arg(str(name), key, raw[key])
        for key, default in feature.optional.items():
            args[key] = _int_arg(str(name), key, raw.get(key, default))
        if name == "sma_ratio" and args["fast"] >= args["slow"]:
            raise SignalSpecError("sma_ratio needs fast < slow")
        if name == "macd" and args["fast"] >= args["slow"]:
            raise SignalSpecError("macd needs fast < slow")
        if name == "volume_ratio" and args["recent"] > args["lookback"]:
            raise SignalSpecError("volume_ratio needs recent <= lookback")
        if 3 * args.get("slow", 0) + args.get("signal", 0) > MAX_LOOKBACK:
            raise SignalSpecError("macd window exceeds the maximum lookback")
        return Node("feature", str(name), args, ())

    if "op" in raw:
        op = str(raw["op"])
        if op in UNARY_OPS:
            if set(raw) != {"op", "of"}:
                raise SignalSpecError(f"{op!r} takes exactly one operand, under 'of'")
            child = parse(raw["of"], _depth=_depth + 1, _count=count)
            return Node("op", op, {}, (child,))
        if op in NARY_OPS:
            operands = raw.get("args")
            if set(raw) != {"op", "args"} or not isinstance(operands, list):
                raise SignalSpecError(f"{op!r} takes a list of operands, under 'args'")
            if len(operands) < 2 or (op in {"sub", "div"} and len(operands) != 2):
                raise SignalSpecError(f"{op!r} has the wrong number of operands")
            children = tuple(parse(c, _depth=_depth + 1, _count=count) for c in operands)
            return Node("op", op, {}, children)
        known = ", ".join(sorted(UNARY_OPS | NARY_OPS))
        raise SignalSpecError(f"unknown op {op!r}; known ops are {known}")

    raise SignalSpecError(f"an expression needs 'feature', 'op' or 'const': {dict(raw)!r}")


def _cross_rank(values: Scores) -> Scores:
    present = sorted((v, t) for t, v in values.items() if v is not None)
    n = len(present)
    out: Scores = dict.fromkeys(values)
    if n == 1:
        out[present[0][1]] = 0.5
        return out
    # Ties share the average of their positions, so a rank never depends on
    # the alphabetical order of two equal names.
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        pct = ((i + j) / 2.0) / (n - 1)
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


def _cross_zscore(values: Scores) -> Scores:
    present = [v for v in values.values() if v is not None]
    sd = _stdev(present)
    if not sd:
        return dict.fromkeys(values)
    m = _mean(present)
    return {t: (None if v is None else (v - m) / sd) for t, v in values.items()}


def evaluate(node: Node, window: PanelWindow, tickers: Sequence[str]) -> Scores:
    """Score every ticker. ``None`` means "cannot be scored now", never zero."""

    if node.kind == "const":
        return dict.fromkeys(tickers, node.value)
    if node.kind == "feature" and node.name in ACCOUNTING_FEATURES:
        return {t: ACCOUNTING_FEATURES[node.name](window, t, node.metric) for t in tickers}
    if node.kind == "feature":
        feature = FEATURES[node.name]
        out: Scores = {}
        for t in tickers:
            raw = feature.compute(window, t, node.args)
            out[t] = raw if raw is not None and math.isfinite(raw) else None
        return out

    parts = [evaluate(c, window, tickers) for c in node.children]
    if node.name == "rank":
        return _cross_rank(parts[0])
    if node.name == "zscore":
        return _cross_zscore(parts[0])

    result: Scores = {}
    for t in tickers:
        vals = [p[t] for p in parts]
        if any(v is None for v in vals):
            result[t] = None
            continue
        xs = [v for v in vals if v is not None]
        value: float | None
        if node.name == "neg":
            value = -xs[0]
        elif node.name == "abs":
            value = abs(xs[0])
        elif node.name == "log":
            value = math.log(xs[0]) if xs[0] > 0.0 else None
        elif node.name == "add":
            value = sum(xs)
        elif node.name == "mul":
            value = math.prod(xs)
        elif node.name == "sub":
            value = xs[0] - xs[1]
        else:  # div
            value = xs[0] / xs[1] if xs[1] != 0.0 else None
        result[t] = value if value is not None and math.isfinite(value) else None
    return result


# --- the strategy ---------------------------------------------------------------


def _period(day: date, rebalance: str) -> tuple[int, ...]:
    if rebalance == REBALANCE_MONTHLY:
        return (day.year, day.month)
    iso = day.isocalendar()
    return (iso.year, iso.week)


def _rebalance_index(window: PanelWindow, rebalance: str) -> int:
    """The bar the book was last chosen on: the first session of this period.

    By date rather than by bar count, so the backtest and the live Runner — whose
    panels start on different days — rebalance on the same calendar sessions.
    """

    if rebalance == REBALANCE_DAILY:
        return window.current_index
    dates = window.dates()
    current = _period(dates[-1], rebalance)
    i = len(dates) - 1
    while i > 0 and _period(dates[i - 1], rebalance) == current:
        i -= 1
    return i


@dataclass(frozen=True, slots=True)
class SignalSpecStrategy:
    """A strategy defined entirely by its spec. See the module docstring."""

    signal: Node
    select: str = SELECT_TOP
    top_n: int = 10
    gross_exposure: float = 1.0
    weighting: str = WEIGHTING_EQUAL
    rebalance: str = REBALANCE_DAILY
    require_sign: bool = False
    """Top-N names must also score above zero, and bottom-N below it.

    Cross-sectional momentum has always done this — a top-ten name with a
    negative formation return is a loser, and holding it would be a different
    strategy — so the option exists to express that rule exactly. With it, a
    weak market holds fewer than N names rather than the least-bad N."""

    def __post_init__(self) -> None:
        if self.select not in SELECTIONS:
            raise SignalSpecError(f"unknown select {self.select!r}; known are {sorted(SELECTIONS)}")
        if self.weighting not in WEIGHTINGS:
            raise SignalSpecError(
                f"unknown weighting {self.weighting!r}; known are {sorted(WEIGHTINGS)}"
            )
        if self.rebalance not in REBALANCES:
            raise SignalSpecError(
                f"unknown rebalance {self.rebalance!r}; known are {sorted(REBALANCES)}"
            )
        lo, hi = PARAM_BOUNDS["top_n"]
        if not lo <= self.top_n <= hi:
            raise SignalSpecError(f"top_n={self.top_n} is outside [{lo:g}, {hi:g}]")
        if not 0.0 <= self.gross_exposure <= 1.0:
            raise SignalSpecError("gross_exposure must lie in [0, 1]")
        if self.weighting == WEIGHTING_INVERSE_VOL and self.select == SELECT_LONG_SHORT:
            raise SignalSpecError(
                "inverse-vol weighting is defined for a long book; on a long/short pair "
                "it would scale the legs independently and break market neutrality"
            )

    @property
    def name(self) -> str:
        return SIGNAL_SPEC_NAME

    @property
    def warmup(self) -> int:
        slack = _REBALANCE_SLACK[self.rebalance]
        vol = INVERSE_VOL_WINDOW + 1 if self.weighting == WEIGHTING_INVERSE_VOL else 1
        return max(self.signal.bars, vol) + slack

    def describe(self) -> str:
        return (
            f"{self.select} {self.top_n if self.select != SELECT_POSITIVE else ''} by "
            f"{self.signal.describe()}, {self.weighting}, rebalanced {self.rebalance}"
        ).replace("  ", " ")

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        at = window.rewind(_rebalance_index(window, self.rebalance))
        tickers = at.live_tickers
        scores = evaluate(self.signal, at, tickers)
        scored = [(v, t) for t, v in scores.items() if v is not None]
        if not scored:
            return dict.fromkeys(window.tickers, 0.0)

        if self.select == SELECT_POSITIVE:
            selected = sorted(t for v, t in scored if v > 0.0)
            return self._weigh(window, at, selected)

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        if self.select == SELECT_LONG_SHORT:
            n = len(scored)
            k = min(self.top_n, n // 2)
            longs = [t for v, t in scored[:k] if not self.require_sign or v > 0.0]
            shorts = [t for v, t in scored[n - k :] if not self.require_sign or v < 0.0]
            return _long_short_weights(window.tickers, longs, shorts, self.gross_exposure)
        if self.select == SELECT_BOTTOM:
            scored.sort(key=lambda pair: (pair[0], pair[1]))
            selected = [t for v, t in scored[: self.top_n] if not self.require_sign or v < 0.0]
        else:
            selected = [t for v, t in scored[: self.top_n] if not self.require_sign or v > 0.0]
        return self._weigh(window, at, selected)

    def _weigh(
        self, window: PanelWindow, at: PanelWindow, selected: list[str]
    ) -> Mapping[str, float]:
        if self.weighting == WEIGHTING_EQUAL:
            return _equal_weights(window.tickers, selected, self.gross_exposure)
        vols = {t: _trailing_vol(at, t, INVERSE_VOL_WINDOW) for t in selected}
        return _inverse_volatility_weights(window.tickers, selected, self.gross_exposure, vols)

    @classmethod
    def from_spec(cls, params: Mapping[str, Any]) -> SignalSpecStrategy:
        if "signal" not in params:
            raise SignalSpecError("a signal-spec strategy needs params.signal")
        allowed = {
            "signal",
            "select",
            "top_n",
            "gross_exposure",
            "weighting",
            "rebalance",
            "require_sign",
        }
        unknown = set(params) - allowed
        if unknown:
            raise SignalSpecError(f"unknown signal-spec param(s) {sorted(unknown)}")
        top_n = params.get("top_n", 10)
        if isinstance(top_n, bool) or not isinstance(top_n, (int, float)) or top_n != int(top_n):
            raise SignalSpecError(f"top_n must be a whole number, got {top_n!r}")
        return cls(
            signal=parse(params["signal"]),
            select=str(params.get("select", SELECT_TOP)),
            top_n=int(top_n),
            gross_exposure=float(params.get("gross_exposure", 1.0)),
            weighting=str(params.get("weighting", WEIGHTING_EQUAL)),
            rebalance=str(params.get("rebalance", REBALANCE_DAILY)),
            require_sign=bool(params.get("require_sign", False)),
        )


__all__ = [
    "ACCOUNTING_FEATURES",
    "FEATURES",
    "MAX_DEPTH",
    "MAX_LOOKBACK",
    "MAX_NODES",
    "NARY_OPS",
    "PARAM_BOUNDS",
    "REBALANCES",
    "SELECTIONS",
    "SIGNAL_SPEC_NAME",
    "UNARY_OPS",
    "WEIGHTINGS",
    "Node",
    "SignalSpecError",
    "SignalSpecStrategy",
    "evaluate",
    "parse",
]
