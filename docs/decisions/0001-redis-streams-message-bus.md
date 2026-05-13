# ADR-0001: Redis Streams as Cross-Department Message Bus

**Status:** Accepted
**Date:** 2026-05-06
**Deciders:** Mike White

## Context

Shrap operates as nine departments, each running as isolated Docker containers on
the Dell. Departments need to communicate asynchronously: a regime change detected
by Research must reach the Trading Floor; Intelligence findings must reach the
Decision Maker; a Risk violation must reach every department simultaneously. A
shared message bus is required. The technology choice determines failure isolation,
replay capability, audit properties, and operational overhead for all
cross-department communication.

## Decision

Redis Streams. Redis runs as a single container on the Dell. Departments publish
to named streams and consume via consumer groups, which support competing consumers,
message acknowledgment, and replay from any position in the log. The append-only
log is a natural audit surface — every cross-department event is preserved by
default.

## Alternatives Considered

**Kafka.** Designed for high-throughput distributed workloads across multiple
brokers. Shrap runs on a single host during the sprint. Kafka's operational overhead
(broker management, KRaft configuration, topic administration) is not justified at
this scale. Eliminated: overkill.

**RabbitMQ.** Strong routing semantics and delivery guarantees, but no native
message replay. Audit and failure recovery require a separate persistence layer.
Eliminated: replay is a requirement.

**NATS.** Fast and lightweight; JetStream adds persistence. Redis is already in the
stack for ephemeral state — adding NATS would be a second dependency with no
meaningful advantage over Redis Streams for this workload. Eliminated: redundant.

**NautilusTrader internal bus only.** Works well within the trading engine. Not
accessible to Python-native agents (LangGraph, OpenHands, Reporting). Not a
general cross-department solution. Eliminated: insufficient scope.

## Consequences

**Enables:** Failure isolation by design — a crash in the Intelligence Department
does not affect the Trading Floor. Consumer groups allow load distribution within a
department. No new infrastructure: Redis is already running for ephemeral state.

**Constrains:** Redis is a single point of failure for cross-department
communication during the sprint. No schema enforcement at the bus layer — the
event envelope schema (separate ADR pending) must be enforced by producers. Large
payloads (filing text, backtest results) must not travel on the bus; stream events
carry references to PostgreSQL or Qdrant records, not the records themselves.

**Cost:** One Redis container on the Dell. Negligible.

## Notes

NautilusTrader has its own internal pub/sub bus for the trading core, handling
order updates, fills, and market data at the latencies the engine requires. That
bus and Redis Streams coexist without conflict. NautilusTrader publishes selected
events — regime signals, fill confirmations, risk alerts — to Redis Streams for
consumption by other departments.
