"""Langfuse tracing for every completion the firm runs (KI-018).

Langfuse has been deployed since Month 1 and nothing has ever written to it. That
is not an idle container: ``docs/infrastructure/llm-routing.md`` builds the whole
cloud-to-local migration path on trace data — *"at least 50 task instances of the
relevant type (recorded in Langfuse with full input/output)"* — and Month 4's exit
criteria need the LLM Migration Evaluator to shadow-evaluate that sample. Neither
is reachable without this module, and the shortfall compounds: an untraced call is
evaluation sample that cannot be recovered afterwards. The Tech Watcher filtered
roughly 1,900 items against several prompt versions and kept nothing of the
model's reasoning beyond the verdict rows.

**Tracing fails open, and that is deliberate.** The Risk Officer fails *closed* —
it refuses to approve against a book it cannot establish, because approving on an
unknown book is the firm's worst failure mode. This is the opposite case. A
tracer that raises would let an observability outage stop the Tech Watcher from
filing, which trades a real capability for a bookkeeping one. Every failure here
is caught, logged as ``llm.trace_failed`` and swallowed.

**Calls are traced inline rather than batched in the background.** A batching
consumer would be faster and would lose whatever sat in its queue when a
container restarted. Since the entire point of the card is sample that cannot be
recovered, durability beats throughput: a completion that returns has already
been recorded. The cost is one local POST against calls that take seconds.

Two things the ingestion contract does that a reasonable implementation would get
wrong, both confirmed against Langfuse's published OpenAPI spec:

- **Success is 207, not 200**, and per the spec the endpoint *"does not return a
  4xx status code for input errors. Instead, it responds with a 207 status code,
  which includes a list of the encountered errors."* So the status line alone
  cannot tell you whether anything was stored — :meth:`LangfuseTracer.record`
  reads the ``errors`` array out of the success body.
- **A batch is capped at 3.5 MB.** A Filing Processor prompt carrying an SEC
  document body can approach that alone, and an oversized batch is rejected
  whole. Fields are clipped to :data:`DEFAULT_MAX_FIELD_CHARS` and the clipping
  is recorded in the trace metadata, because llm-routing.md asks for *full*
  input/output and a silently shortened sample would satisfy the letter of that
  while quietly corrupting the migration evaluation.

**Why this is hand-rolled rather than the Langfuse SDK.** The deployed image is
``langfuse/langfuse:2``, and Langfuse's own compatibility matrix rules out every
current client against it:

===========================  ==========================================
Client                       OSS v2 (this deployment)
===========================  ==========================================
Python SDK v4 (current)      Unsupported
Python SDK v3                Unsupported
Python SDK v2                Full — but deprecated
OTel ``/api/public/otel/*``  Unsupported (needs server >= 3.22.0)
``/api/public/ingestion``    **Full**
===========================  ==========================================

So the legacy ingestion endpoint is not a shortcut around the SDK; on this server
it is the only supported path, and the SDK the docs recommend cannot talk to it
at all. **OSS v2 is also marked end of life**, which is a real finding rather
than a footnote — see KI-032. Upgrading to v3+ adds a worker container,
ClickHouse, a blob store and Redis, so it is an infrastructure card and the
decision is Mike's.

**On masking, which the baseline asks about.** Nothing is masked, and that is an
assessment rather than an omission. What reaches this layer is public-source
text: SEC filing bodies, news headlines, arXiv abstracts, and the firm's own
prompts. No credential passes through it — ADR-0003 confines broker keys to
broker-facing containers, and the LLM path is not one of them. Two things would
change the answer and should reopen it: a prompt built from anything
user-supplied, or a Langfuse instance reachable by anyone but Mike.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from ulid import ULID

from shrap.llm.http import HTTPClient

log = structlog.get_logger(__name__)

DEFAULT_HOST = "http://langfuse:3000"
"""The compose service. Langfuse is not published off-box (ADR-0004)."""

DEFAULT_TIMEOUT_SECONDS = 5.0
"""Short on purpose. This is a same-host POST, and a slow tracer must never
become a slow agent — the timeout is the ceiling on what observability can cost
a completion that already succeeded."""

DEFAULT_MAX_FIELD_CHARS = 250_000
"""Per input/output field, well inside the 3.5 MB batch cap even when a prompt
and its completion are both at the limit."""

INGESTION_PATH = "/api/public/ingestion"

UNIT_TOKENS = "TOKENS"
LEVEL_DEFAULT = "DEFAULT"
LEVEL_ERROR = "ERROR"

_OK_STATUS = frozenset({200, 201, 207})


@dataclass(frozen=True, slots=True)
class TracingConfig:
    """Where traces go and how much of each call is allowed through."""

    host: str
    public_key: str
    secret_key: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS
    release: str | None = None

    @property
    def ingestion_url(self) -> str:
        return f"{self.host.rstrip('/')}{INGESTION_PATH}"

    @property
    def authorization(self) -> str:
        """HTTP Basic, public key as user and secret key as password."""

        raw = f"{self.public_key}:{self.secret_key}".encode()
        return f"Basic {base64.b64encode(raw).decode('ascii')}"


@dataclass(frozen=True, slots=True)
class TracedCall:
    """One completion attempt, whether or not it returned anything.

    Failures are recorded too. They are not evaluation sample — there is no
    output to judge — but they are the only record of what the model layer costs
    in reliability, and principle 8 is "audit everything". A failed call arrives
    in Langfuse at ``level=ERROR`` carrying the exception text and no output.
    """

    task: str
    """What the call was *for*, named verb-first — ``score-filing-item``.

    Two constraints meet in this field. llm-routing.md slices the migration
    sample "by task type and regime", so a trace named after its *tier* would be
    unsliceable: several unrelated jobs share ``local-classification``. And
    Langfuse's tracing guidance asks for active language, verbs first, at low
    cardinality — ``score-news-item``, never ``score-news-item-8945``. Anything
    run-specific belongs in :attr:`metadata`, which is why the item id lives
    there and not in the name.
    """

    tier: str
    provider: str
    model: str
    prompt: str
    system: str | None
    started_at: datetime
    ended_at: datetime
    content: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    trace_id: str | None = None
    """Join this generation onto an existing trace instead of minting one.

    A trace is meant to be "one self-contained unit of work", and for most of
    the firm that is one completion. The exception is the escalation path: the
    Filing Processor and News Analyzer score an item locally and, when it looks
    material, score the *same item* again on a cloud tier. That is one unit of
    work with two generations, not two units — so the caller mints one id and
    passes it to both legs, and the second leg shows up under the first's trace
    rather than as an unrelated row.

    Re-sending ``trace-create`` with an id already seen is an upsert, so the
    trace ends up carrying the last leg's output — which for an escalation is
    the verdict that actually won.
    """

    session_id: str | None = None
    """Groups every trace from one pass.

    A filter pass over 300 items produces 300 traces. Individually correct and
    collectively unreadable: nothing in the UI says they were one run, so "what
    did the 09:00 pass do" cannot be asked. Sessions are how Langfuse expects
    that to be expressed, and the pass is the natural unit — one session per
    pass, one trace per item.
    """

    @property
    def failed(self) -> bool:
        return self.error is not None


class LangfuseTracer:
    """Post one completion to Langfuse as a trace plus a generation."""

    def __init__(self, http: HTTPClient, config: TracingConfig) -> None:
        self._http = http
        self._config = config

    async def record(self, call: TracedCall) -> None:
        """Record ``call``, or log why it could not be recorded. Never raises."""

        try:
            await self._record(call)
        except Exception:
            # Deliberately broad. Anything at all from the tracer — a connection
            # refused, a malformed body, a bug in this module — must not reach
            # the agent that made the completion.
            log.warning(
                "llm.trace_failed",
                task=call.task,
                model=call.model,
                exc_info=True,
            )

    async def _record(self, call: TracedCall) -> None:
        trace_id = call.trace_id or str(ULID())
        prompt, prompt_clipped = _clip_text(call.prompt, self._config.max_field_chars)
        system, system_clipped = _clip(call.system, self._config.max_field_chars)
        output, output_clipped = _clip(call.content, self._config.max_field_chars)

        metadata: dict[str, Any] = {
            **dict(call.metadata),
            "tier": call.tier,
            "provider": call.provider,
            "latency_ms": round(_duration_ms(call), 1),
        }
        # Say so when a field was shortened. The migration evaluator can then
        # exclude the sample rather than train on a truncated one.
        if prompt_clipped or system_clipped:
            metadata["input_truncated"] = True
        if output_clipped:
            metadata["output_truncated"] = True

        trace_body: dict[str, Any] = {
            "id": trace_id,
            "name": call.task,
            "timestamp": _iso(call.started_at),
            "input": _input_payload(system, prompt),
            "output": output,
            "metadata": metadata,
            "tags": [call.tier, call.provider, call.model],
        }
        if call.session_id is not None:
            trace_body["sessionId"] = call.session_id
        if self._config.release is not None:
            trace_body["release"] = self._config.release

        generation_body: dict[str, Any] = {
            "id": str(ULID()),
            "traceId": trace_id,
            "name": call.task,
            "startTime": _iso(call.started_at),
            "endTime": _iso(call.ended_at),
            "model": call.model,
            "modelParameters": dict(call.model_parameters),
            "input": _input_payload(system, prompt),
            "output": output,
            "metadata": metadata,
            "level": LEVEL_ERROR if call.failed else LEVEL_DEFAULT,
        }
        if call.error is not None:
            generation_body["statusMessage"] = call.error
        usage = _usage(call)
        if usage is not None:
            generation_body["usage"] = usage

        batch = [
            _envelope("trace-create", trace_body),
            _envelope("generation-create", generation_body),
        ]
        response = await self._http.post(
            self._config.ingestion_url,
            json={"batch": batch},
            timeout=self._config.timeout_seconds,
            headers={
                "Authorization": self._config.authorization,
                "Content-Type": "application/json",
            },
        )
        if response.status_code not in _OK_STATUS:
            log.warning(
                "llm.trace_rejected",
                task=call.task,
                status=response.status_code,
                body=response.text[:500],
            )
            return

        # A 207 carries per-event outcomes in its body, so "the POST worked" and
        # "the trace was stored" are different questions and only the body
        # answers the second one.
        errors = _ingestion_errors(response)
        if errors:
            log.warning(
                "llm.trace_rejected",
                task=call.task,
                status=response.status_code,
                errors=errors[:3],
            )
            return

        log.debug("llm.traced", task=call.task, model=call.model, trace_id=trace_id)


def tracing_config_from_env(env: Mapping[str, str]) -> TracingConfig | None:
    """Build a config, or ``None`` when this deployment has no Langfuse keys.

    Both keys are required and neither has a default. Langfuse issues them per
    project from its own UI, so there is nothing sensible to fall back to and a
    deployment without them is simply one that does not trace. Absence is the
    off switch — no separate ``LANGFUSE_ENABLED`` flag to disagree with it.
    """

    public_key = env.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = env.get("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key:
        return None
    return TracingConfig(
        host=env.get("LANGFUSE_HOST", "").strip() or DEFAULT_HOST,
        public_key=public_key,
        secret_key=secret_key,
        timeout_seconds=_float_or(env.get("LANGFUSE_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS),
        max_field_chars=_int_or(env.get("LANGFUSE_MAX_FIELD_CHARS"), DEFAULT_MAX_FIELD_CHARS),
        release=env.get("LANGFUSE_RELEASE", "").strip() or None,
    )


def tracer_from_env(env: Mapping[str, str], http: HTTPClient) -> LangfuseTracer | None:
    """The one line every agent adds to start tracing.

    Says out loud which way it resolved. A deployment that believes it is tracing
    and is not would reproduce KI-018 exactly, so the disabled case is logged at
    ``info`` rather than passing silently.
    """

    config = tracing_config_from_env(env)
    if config is None:
        log.info(
            "llm.tracing_disabled",
            reason="LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not both set",
        )
        return None
    log.info("llm.tracing_enabled", host=config.host)
    return LangfuseTracer(http, config)


def _envelope(event_type: str, body: dict[str, Any]) -> dict[str, Any]:
    """Wrap a body in the ingestion event envelope.

    The envelope id deduplicates retries and the spec asks for a UUID v4, so it
    is one — the ULIDs elsewhere in this module are body ids, where being
    time-sortable is worth more than matching a documented format.
    """

    return {
        "id": str(uuid4()),
        "type": event_type,
        "timestamp": _iso(datetime.now(UTC)),
        "body": body,
    }


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _duration_ms(call: TracedCall) -> float:
    return (call.ended_at - call.started_at).total_seconds() * 1000.0


def _input_payload(system: str | None, prompt: str) -> Any:
    """What the model was asked, in the shape Langfuse renders as a chat."""

    if system is None:
        return [{"role": "user", "content": prompt}]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]


def _usage(call: TracedCall) -> dict[str, Any] | None:
    """Token counts, when the provider reported them.

    Ollama omits the counts on some responses, and a usage block claiming zero
    tokens is worse than none: it is indistinguishable from a real zero and it
    would drag any cost total computed from these traces downwards.
    """

    if call.input_tokens is None and call.output_tokens is None:
        return None
    usage: dict[str, Any] = {"unit": UNIT_TOKENS}
    if call.input_tokens is not None:
        usage["input"] = call.input_tokens
    if call.output_tokens is not None:
        usage["output"] = call.output_tokens
    if call.input_tokens is not None and call.output_tokens is not None:
        usage["total"] = call.input_tokens + call.output_tokens
    return usage


def _ingestion_errors(response: Any) -> list[Any]:
    """The ``errors`` array out of a 207 body, or empty when there is none."""

    try:
        payload = response.json()
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    errors = payload.get("errors")
    return errors if isinstance(errors, list) else []


def _clip_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _clip(text: str | None, limit: int) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    return _clip_text(text, limit)


def _float_or(raw: str | None, fallback: float) -> float:
    try:
        return float(raw) if raw is not None and raw.strip() else fallback
    except ValueError:
        return fallback


def _int_or(raw: str | None, fallback: int) -> int:
    try:
        return int(raw) if raw is not None and raw.strip() else fallback
    except ValueError:
        return fallback


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MAX_FIELD_CHARS",
    "DEFAULT_TIMEOUT_SECONDS",
    "INGESTION_PATH",
    "LangfuseTracer",
    "TracedCall",
    "TracingConfig",
    "tracer_from_env",
    "tracing_config_from_env",
]
