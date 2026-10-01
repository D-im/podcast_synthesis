# Story 2.3: How far to trust the Fidelity Score

Date: 2026-09-30. Checker: OpenAI `gpt-6.1-sol` through the app's `OpenAIVerifier` (claims, ideas and coverage passes). Fixtures: `tests/fixtures/planted/` (a made-up ferry-authority interview of 31 segments and 332 words, a One-Pager with 14 true claims, 8 planted false claims and one omitted idea, and an answer key). Command: `RUN_NETWORK_TESTS=1 RUN_PAID_CHECKER_TEST=1 PLANTED_RUNS=3 uv run pytest -s tests/test_planted_errors.py -k real_checker`. The same fixtures were run twice (two sets of three runs, before and after review changes to the matching helper); both sets gave the same result, and the second set is reproduced below.

## Findings

| What was tested | Result over 3 runs (and 3 earlier runs, 6 in all) |
|---|---|
| Planted false claims caught (wrong number x2, wrong speaker, fabricated fact, reversed meaning, exaggeration, invented quote, wrong entity) | **8 of 8 in every run**, 48 of 48 in all six |
| True claims wrongly flagged as unsupported | **0 of 14 in every run** |
| Key claims the checker never listed | none |
| The omitted idea (the fishermen's co-op compromise) | **found in every run** by the ideas pass ("Construction compromise protects herring season" or similar) and rated **missing** every time |
| Cost per run (three paid calls) | $0.0369 to $0.0412; about $0.23 for all six runs plus the first attempt |

## What this does and does not show

- The checker is reliable at the kinds of error planted here, including the subtle ones (a softened "most" turned into "all", a reversed regret, a host credited with the guest's statement, a one-letter company name change, and an invented quote). In none of the six runs did it let one through or invent a problem with a true claim.
- **This is the easy case.** The transcript is short and clean, the facts are crisp numbers and names, each planted error is a single sentence that can be compared with one transcript line, and the setting is invented so outside knowledge cannot help. The real episode (65 minutes, German and English, garbled names, argued positions) was much harder: on it the checker flagged 10 of 95 claims, and a manual look showed most of those were real softening, a few were subjective wording ("prominent", "one-sided"), and one was a true claim marked unsupported because the checker's quote was not found. So this result says the mechanism works; it does not say the real-world rate of misses is zero.
- **The accuracy number is not "share of true claims".** The checker lists claims its own way, splitting and adding some (for example "Dana Okafor is the guest" separately, or the subjective "Listeners who run transport infrastructure will get the most from the episode"). Accuracy therefore moved between 60.9% and 66.7% over runs on the same One-Pager, while the planted errors were caught identically. Use the unsupported-claims list, not the percentage, as the signal.
- **Coverage is rough.** The omission was found and rated missing every time, but the overall coverage varied 56% to 63% because the checker rates other, fully covered ideas as partial whenever a detail is left out (the same strictness seen on the real episode).
- Not tested: errors that need real-world knowledge, long-range inconsistencies across a long transcript, transcripts with speech-recognition errors, claims about tone, and an omitted idea that is only partly present.

## How far to trust it

- **Trust the unsupported-claims list as a strong pointer.** Anything it flags deserves a look; on clear factual errors it caught every planted one in every run.
- **Do not read silence as proof.** A claim the checker did not flag may still be wrong in a way this test does not cover, and the checker is not deterministic.
- **Do not compare scores across episodes or runs.** Accuracy depends on how the checker splits claims, and coverage is relative to the ideas it chose.
- Use it as a second opinion that tells you where to look, not as a stamp of approval. Your own Verdicts (Story 2.5) are the real ground truth for what the score means in practice.

## Full report of the final three runs

```text
Planted-error check, run 1
==========================
Planted false claims caught: 8 of 8
  caught: F01, F02, F03, F04, F05, F06, F07, F08
  missed: none
True claims wrongly flagged: 0 of 14
Key claims the checker never listed: none
Checker claims matching no key claim: 2
  - Dana Okafor is the guest.
  - Listeners who run transport infrastructure will get the most from the episode.
Omitted idea 'The fishermen's co-op compromise on pile driving': found as 'Construction compromise protects herring season', coverage missing
Fidelity: accuracy 62.5%, coverage 56.2%
Cost: $0.0387 (3 paid calls)
Per claim:
  T01 [true] -> supported
  T02 [true] -> supported
  F01 [false/fabricated fact] -> unsupported
  T03 [true] -> supported
  F02 [false/wrong number] -> unsupported
  T04 [true] -> supported
  T05 [true] -> supported
  F03 [false/wrong number] -> unsupported
  T06 [true] -> supported
  T07 [true] -> supported
  F04 [false/wrong entity] -> unsupported
  T08 [true] -> supported
  T09 [true] -> supported
  T10 [true] -> supported
  T11 [true] -> supported
  F05 [false/exaggeration] -> unsupported
  F06 [false/wrong speaker] -> unsupported
  F07 [false/reversed meaning] -> unsupported
  T12 [true] -> supported
  T13 [true] -> supported
  T14 [true] -> supported
  F08 [false/invented quote] -> unsupported

Planted-error check, run 2
==========================
Planted false claims caught: 8 of 8
  caught: F01, F02, F03, F04, F05, F06, F07, F08
  missed: none
True claims wrongly flagged: 0 of 14
Key claims the checker never listed: none
Checker claims matching no key claim: 1
  - Listeners who run transport infrastructure will get the most from the episode.
Omitted idea 'The fishermen's co-op compromise on pile driving': found as 'Construction compromise protects herring season', coverage missing
Fidelity: accuracy 60.9%, coverage 62.5%
Cost: $0.0392 (3 paid calls)
Per claim:
  T01 [true] -> supported
  T02 [true] -> supported
  F01 [false/fabricated fact] -> unsupported
  T03 [true] -> supported
  F02 [false/wrong number] -> unsupported
  T04 [true] -> supported
  T05 [true] -> supported
  F03 [false/wrong number] -> unsupported
  T06 [true] -> supported
  T07 [true] -> supported
  F04 [false/wrong entity] -> unsupported
  T08 [true] -> supported
  T09 [true] -> supported
  T10 [true] -> supported
  T11 [true] -> supported
  F05 [false/exaggeration] -> unsupported
  F06 [false/wrong speaker] -> unsupported
  F07 [false/reversed meaning] -> unsupported
  T12 [true] -> supported
  T13 [true] -> supported
  T14 [true] -> supported
  F08 [false/invented quote] -> unsupported

Planted-error check, run 3
==========================
Planted false claims caught: 8 of 8
  caught: F01, F02, F03, F04, F05, F06, F07, F08
  missed: none
True claims wrongly flagged: 0 of 14
Key claims the checker never listed: none
Checker claims matching no key claim: 3
  - Dana Okafor is the guest.
  - Okafor says the authority ordered two battery-electric ferries in 2022.
  - Listeners who run transport infrastructure will get the most from the episode.
Omitted idea 'The fishermen's co-op compromise on pile driving': found as 'Construction compromise protects herring season', coverage missing
Fidelity: accuracy 66.7%, coverage 61.1%
Cost: $0.0412 (3 paid calls)
Per claim:
  T01 [true] -> supported
  T02 [true] -> supported
  F01 [false/fabricated fact] -> unsupported
  T03 [true] -> supported
  F02 [false/wrong number] -> unsupported
  T04 [true] -> supported
  T05 [true] -> supported
  F03 [false/wrong number] -> unsupported
  T06 [true] -> supported
  T07 [true] -> supported
  F04 [false/wrong entity] -> unsupported
  T08 [true] -> supported
  T09 [true] -> supported
  T10 [true] -> supported
  T11 [true] -> supported
  F05 [false/exaggeration] -> unsupported
  F06 [false/wrong speaker] -> unsupported
  F07 [false/reversed meaning] -> unsupported
  T12 [true] -> supported
  T13 [true] -> supported
  T14 [true] -> supported
  F08 [false/invented quote] -> unsupported
```
