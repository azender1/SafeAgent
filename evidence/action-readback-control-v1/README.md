# SafeAgent Control action-readback comparison

This is the published adapter and sanitized aggregate result for the bounded
comparison discussed in `crewAIInc/crewAI#5802`.

Source evidence is pinned to `mstevens843/crashpoint` commit
`bb9cd47c4b0b02527aab7b369d17b32829cc4e20`, bundle
`action_readback_self_reviewed_v2`. Its offline verifier reported 18 trials, a
valid manifest receipt, and zero verification problems.

The adapter does not modify or invoke SafeAgent Control's frozen reconciliation
core. It maps the external evidence contract as follows:

| External result | Control result |
|---|---|
| One effect, matching admitted payload | `CONFIRMED` |
| Complete readback, no effect | `MISSING_EXTERNALLY` |
| Duplicate or mismatched effect | `CONTRADICTION` |
| Readback unavailable | `UNCERTAIN` |

`MISSING_EXTERNALLY` is an observation at the fixture's terminal boundary. It
does not authorize replay. A fixture prediction marked `passed` is not itself
confirmation.

Run the adapter tests from this directory:

```bash
python -m pytest -q test_action_readback_adapter.py
```

The committed `sanitized_result.json` contains aggregate classifications only.
It excludes logical-action IDs, payload digests, provider/subject identifiers,
and credentials.
