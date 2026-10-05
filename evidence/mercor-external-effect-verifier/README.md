# SafeAgent external-effect verifier prototype for Mercor-style agent evals

This is a **prototype adapter based only on Mercor's public verifier model**. It
is not an official Mercor integration and does not imply endorsement.

Mercor publicly describes a verifier as an isolated program or agent that
inspects an agent run and returns a normalized score in `[0,1]` plus an
explanation. Verifiers may grade trajectory behavior, output, external state
changes, or combinations of them.

SafeAgent Control is aimed at one narrow external-state question:

> Did the agent's recorded execution history agree with what actually happened
> in the authoritative downstream system after retries, timeouts, restarts, or
> delayed confirmations?

This adapter turns bounded SafeAgent Control classifications into a
Mercor-shaped verifier result without changing the frozen Control core.

## Input

A JSON document with a `findings` array. Each finding contains:

- `control_result`: one of `CONFIRMED`, `MISSING_EXTERNALLY`,
  `CONTRADICTION`, or `UNCERTAIN`
- optional `case`: sanitized case label

Example:

```json
{
  "findings": [
    {"case": "invoice-1", "control_result": "CONFIRMED"},
    {"case": "invoice-2", "control_result": "CONTRADICTION"}
  ]
}
```

## Output

The verifier returns:

- `score`: confirmed findings divided by all findings
- `passed`: true only when every finding is `CONFIRMED`
- `scope`: `external_state_changes`
- `explanation`: aggregate counts and the exact failure classes
- `blocking_reasons`: every non-confirmed case
- `replay_authorized`: always false

The score is intentionally conservative. An `UNCERTAIN` result is not treated
as success, and neither `MISSING_EXTERNALLY` nor `CONTRADICTION` authorizes
replay.

## Run

```bash
python mercor_external_effect_verifier.py synthetic_run.json
python -m pytest -q test_mercor_external_effect_verifier.py
```

The purpose of this prototype is to make one integration conversation concrete:
SafeAgent can act as a deterministic external-effect verifier beside Mercor's
existing trajectory/output evaluators, using historical exports first and no
production write access.
