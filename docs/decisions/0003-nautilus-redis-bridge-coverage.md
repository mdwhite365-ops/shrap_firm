# ADR-0003: NautilusTrader-to-Redis Event Bridge Coverage

**Status:** Open
**Date:** 2026-05-06
**Deciders:** Mike White

## Context

The architecture relies on NautilusTrader publishing selected events —
fills, position updates, risk alerts, regime signals — to Redis Streams
so that other departments can consume them without needing direct broker
API access. Section 11 of the architecture document documents broker
credential isolation as a security property: Alpaca and IBKR credentials
are held only by NautilusTrader's container, and all other departments
receive trading data via Redis events rather than direct broker calls.

This isolation property holds only if NautilusTrader's Redis bridge is
comprehensive enough that no department has a legitimate reason to need
direct broker access. The completeness of that bridge — specifically
whether fill data, account state, and position updates are fully and
reliably exposed via Redis events — has not been verified against
NautilusTrader's actual adapter capabilities and event model.

If the bridge has gaps, the isolation model breaks: a department would
need direct broker credentials to get data it cannot get from the bus,
which undermines the security boundary and the audit trail.

## Decision

Pending — will be resolved during Trading Floor agent specification.

The Trading Floor spec will define the exact set of events NautilusTrader
publishes to Redis Streams, verify those events are sufficient for all
consuming departments, and document any gaps that require either bridge
extension or architectural adjustment.

## Alternatives Considered

Not evaluated until the Trading Floor spec establishes the actual event
surface.

## Consequences

Until resolved: the broker credential isolation model described in the
architecture document is an intention, not a verified guarantee. The
Trading Floor spec is the decision point.

## Notes

Flagged during architecture doc drafting (section 11). Tracked here so
the question survives to the Trading Floor spec rather than getting lost
in section 11's prose.
