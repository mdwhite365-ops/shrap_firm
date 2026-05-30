# LITE / Photonics Backwards-Test Rubric — Bottleneck Scout

**Version:** 0.1 (draft)
**Date:** 2026-05-30
**Owner:** Mike White
**Status:** Draft
**Serves:** ADR-0007 (Research thesis: world-changer + infra)
**Subject under test:** `docs/agents/research/bottleneck-scout.md`

---

## 1. Purpose

A backwards-test exists separately from the forward-test for one reason:
to debug methodology before any forward bets are locked. Specifically it
calibrates (a) source-coverage — is the Scout actually reaching the
filings, preprints, and conference talks where binding-bottleneck
evidence first appears, (b) triangulation rules — does it cross-confirm
across SEC / arXiv / conference / patent before promoting, and (c)
discrimination — does it kill hype candidates that look like
bottlenecks but aren't.

This document is explicit that the backwards-test is **debug-grade, not
edge-grade**. The LLM operating the Scout has training data extending
past the cutoff and almost certainly "knows" the answer. We control for
that imperfectly (§4). The load-bearing edge claim for the Research
department is the forward-test running from May 2026 onwards. Passing
this rubric unlocks Phase 1 implementation; it does not prove the
Scout has alpha.

---

## 2. Test setup

**(a) Data cutoff:** 2024-08-31. All evidence the Scout cites must be
dated on or before this day.

**(b) Source set** (the Scout is told to draw only from these):
- SEC EDGAR filings (10-K, 10-Q, 8-K, S-1, DEF 14A) with filing date
  ≤ 2024-08-31
- arXiv preprints with submission date ≤ 2024-08-31
- Conference talks: OFC 2024 (March), Hot Chips 2024 (August),
  ISSCC 2024 (February), SC 2023 (November)
- USPTO patent publications with publication date ≤ 2024-08-31
- Earnings call transcripts through Q2 2024 (calls held by mid-Aug 2024)
- IRDS 2023 roadmap edition
- OCP Global Summit 2023 (October 2023) materials

**(c) Target world-changer fed as input context:** NVIDIA AI compute.
This was already a Mike-promoted world-changer in the Aug-31-2024 state
of the system. The Scout's job is to surface the binding infra
bottleneck downstream of it.

**(d) Infrastructure Mapper graph state at Aug 31 2024:**
- Networking-optical layer is populated with incumbents: Cisco (CSCO),
  Arista (ANET), Broadcom (AVGO), Marvell (MRVL).
- LITE, COHR, FN, AOI are **not** present as primary photonics-pure
  plays. Surfacing them as concrete beneficiaries is the Scout's job.
- HBM / packaging layer is populated with SK Hynix, Micron, Samsung,
  TSMC to avoid degenerate "Scout finds HBM" trivial answers.

---

## 3. Knowledge-contamination controls

The honest framing: the LLM has post-cutoff training data and may
output the right answer for the wrong reason. We mitigate, we do not
eliminate.

**(a) Prompt instruction.** The Scout system prompt explicitly says:
"Reason only from the source set listed below. Do not use any
knowledge of events, papers, filings, or announcements dated after
2024-08-31. If you find yourself reaching for a fact you cannot
anchor to a pre-cutoff source, drop it."

**(b) Blind-coding via red herrings.** The evaluator — not the Scout —
inserts 3–5 contemporaneous bottleneck candidates that did **not** pan
out as binding by May 2026. Default red-herring slate (Mike may
substitute):
1. SiC power devices as the binding constraint on AI inference
2. Optical compute (matrix-mult in the photonic domain) as near-term
   replacement for GPU MAC
3. Analog in-memory compute as near-term replacement for SRAM/HBM
   bandwidth wall
4. Cryogenic CMOS for AI accelerators
5. 3D NAND density as the binding storage constraint on training

The Scout sees these as candidate bottlenecks mixed in with the real
field. The rubric grades whether it promotes them to "binding" or
correctly demotes/kills them.

**(c) Evidence trail required.** Every promoted bottleneck must come
with: SEC filing accession numbers + dates, arXiv IDs + submission
dates, conference session titles + dates, patent publication numbers +
dates. The evaluator spot-checks every citation. A citation that
resolves to a post-cutoff source, or that does not resolve at all,
counts as a fabrication for grading purposes (§7).

**(d) Honest disclosure.** This does not fully escape contamination.
A sufficiently good LLM will retro-fit a plausible pre-cutoff evidence
trail to an answer it already knows. That is exactly why this is a
methodology debug, not an edge proof. Treat verdicts accordingly.

---

## 4. Pass criteria (must hit ALL)

P1. Names the binding bottleneck as one of: "copper interconnect
    signal integrity", "PAM4 signal integrity", or "reach × bandwidth
    wall". Generic "data center scaling" or "interconnect" without the
    copper / PAM4 / reach-bandwidth specificity does **not** count.

P2. Names **both** co-packaged optics (CPO) **and** linear pluggable
    optics (LPO) as the replacement layer. Both terms must appear and
    be distinguished.

P3. Surfaces at minimum 3 of {LITE, COHR, FN, AOI} as concrete
    public-company beneficiaries, with ticker or full name.

P4. Surfaces ANET or NVDA as having silicon photonics integration as
    a related (not pure-play) bet.

P5. Every evidence citation the Scout offers resolves to a source
    dated ≤ 2024-08-31 and is verifiable (filing exists, arXiv ID
    resolves, conference session is real).

P6. Discriminates against the red-herring slate: none of the inserted
    red herrings is promoted to "binding". Demoting them to "watch" or
    "kill" is fine; promoting any to "binding" fails this criterion.

P7. Time-to-bind estimate falls in the 6–18 month band measured from
    August 2024. (Ground truth: binding played out from late 2024
    through 2026.)

All seven required for PASS.

---

## 5. Partial-pass criteria

One or more of P1–P7 missing, **but the core thesis is present**
(meaning at minimum P1 OR P2 satisfied AND P3 satisfied at the 1–2
company level). Specifically:

PP1. Names the bottleneck correctly (P1 yes) but misses the CPO/LPO
     distinction (P2 partial — see §7 gray-area).
PP2. Surfaces 1 or 2 of {LITE, COHR, FN, AOI} but misses the rest.
PP3. Identifies CPO/LPO as the replacement layer but cites at least
     one post-cutoff source (P5 violated, but the answer shape is
     right).
PP4. Time-to-bind estimate is in the wider 3–36 month range but
     outside the tight 6–18 band.
PP5. Red herrings correctly killed but the Scout's stated reason for
     killing them is weak or post-hoc.

Any combination of these that does not trigger a §6 fail clause →
PARTIAL.

---

## 6. Fail criteria (ANY triggers FAIL)

F1. Does not surface photonics-as-replacement at all.
F2. Names the wrong bottleneck as binding — e.g. flags HBM bandwidth,
    power delivery, cooling, or packaging yield as **the** binding
    wall, not copper interconnect / PAM4 SI.
F3. Promotes any red-herring candidate to "binding".
F4. One or more evidence citations are fabricated (do not resolve) or
    unverifiable.
F5. Surfaces the right answer (P1–P4) with no evidence trail attached.
    Right answer + no sources = fail, because the whole point is to
    verify the Scout reached the sources rather than pattern-matched
    on training data.

---

## 7. Edge / gray-area rulings

Spelled out so two evaluators converge:

- Names photonics generically ("optics will replace copper") without
  distinguishing CPO from LPO → **partial** (PP1).
- Names CPO but misses LPO, or vice versa → **partial** (PP1).
- Surfaces TSMC SoIC / CoWoS-L packaging as the bottleneck → **partial**.
  Close adjacency (advanced packaging is a real concurrent constraint)
  but not the binding wall this rubric is testing for. Does not fail.
- Surfaces HBM3E supply as **a** bottleneck alongside copper SI →
  acceptable, does not affect grade. Surfaces HBM as **the** binding
  wall instead of copper SI → **fail** (F2).
- Names Broadcom (AVGO) or Marvell (MRVL) silicon photonics roadmap
  in addition to the pure-plays → counts toward P4 if ANET/NVDA also
  mentioned; does not substitute for P3 because they are incumbents
  already in the graph.
- Cites a Q3 2024 earnings call (held in Oct/Nov 2024) → post-cutoff,
  counts as a P5 violation per-citation.
- Cites OFC 2025 (March 2025) → post-cutoff, P5 violation.
- Cites OFC 2024 (March 2024) → in-window, valid.
- Scout outputs a probability/confidence on each promotion → not
  required, but if present, miscalibrated confidence (>80% on a wrong
  answer) is logged as a methodology note, not a fail.

---

## 8. Evaluator process

1. Eval reviewer is Mike, or a Sonnet subagent given this rubric plus
   the raw Scout output and the source set manifest.
2. Red herrings are inserted by the evaluator, not the Scout. The
   Scout sees a mixed candidate list; the evaluator holds the key.
3. **Two independent gradings required** before a verdict is locked.
   Two Sonnet subagents, or Mike + one Sonnet, both produce a section-
   by-section score (P1–P7, F1–F5, PP1–PP5 noted where relevant).
   Disagreement on the top-line verdict triggers a third grading and
   a written reconciliation note.
4. Verdict + section-by-section score + reconciliation note (if any)
   is appended to `docs/research/calibration.md` under section
   **(b) Bottleneck Scout forward-test ledger**. The rubric
   explicitly notes the backwards-test entries land in the
   forward-test ledger despite the name — the ledger is the single
   accountability surface for the Scout, and splitting it would let
   debug runs hide from the calibration math.

---

## 9. What pass / partial / fail unlocks

- **FAIL** → Bottleneck Scout spec needs a rewrite of detection
  criteria before any code is written. Specifically: which sources
  the Scout pulls from, how it triangulates, and what its kill rules
  are. No Phase 1 implementation until a re-run on a revised spec
  reaches at least PARTIAL.
- **PARTIAL** → Code can be written, but the verdict ships with an
  explicit list of detection-rule gaps (which Pn's were missed and
  why) that v0.2 of the spec must address. The gap list is appended
  to the ledger entry.
- **PASS** → Proceed to Phase 1 implementation with the Scout spec
  as-written. The forward-test from May 2026 onwards is then the
  load-bearing edge claim; this PASS is not treated as evidence of
  alpha, only as evidence that the methodology is not obviously
  broken.

---

## 10. Open questions (flag for Mike)

- Should the red-herring slate be Mike-supplied (curated to be
  genuinely contemporaneous) or auto-derived from a sweep of
  trade-press hype headlines in the Jun–Aug 2024 window? Auto-derived
  is more honest (less curator bias) but noisier and harder to
  reproduce.
- Should the eval be Mike personally on the first run, or two Sonnet
  subagents from the start? Mike-on-first-run gives a sanity anchor
  but is not scalable; two-Sonnet from the start is scalable but
  leaves no human anchor on the calibration of the evaluator itself.
- Is the 6–18 month time-to-bind window the right band? It matches
  the observed unfold (late-2024 through 2026) but is narrow enough
  that a Scout that's directionally right but slow on timing fails
  P7. Looser band (3–24 months) would let timing-fuzzy passes
  through; tighter band (6–12) would penalise the actual observed
  timing.
- Should "fabricated citation" (F4) trigger an immediate fail of the
  whole run, or just void the affected promotion and let the rest of
  the output be graded? Current draft: immediate fail, because
  fabrication is a methodology break, not a local error.

---

*End of rubric v0.1.*
