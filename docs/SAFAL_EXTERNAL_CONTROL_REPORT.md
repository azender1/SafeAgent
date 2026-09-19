# Safal external control comparison

## Decision

The two public wallet-watch bundles are valid external control inputs, but they
are not claim/order reconciliation inputs. They contain no SafeAgent claim and
no pre-dispatch payment-intent identifier. The observed `event.event_id` remains
an Ethereum log identity and is never reinterpreted as a logical action ID.

The frozen SafeAgent Control reconciliation core was not modified.

## Integrity verification

- Both downloaded archives matched their published `SHA256SUMS` manifests.
- Successful bundle verifier at source `e2d644155b587da89b12116a9b94150c6e5d4837`:
  `PASS`, 80 files, 26 captured RPC responses, 3 offline CLI replays,
  checkpoint 25,920,399 to 25,920,400, one retained history event.
- Incomplete bundle verifier at the same source:
  bundle integrity `PASS`, live status `INCOMPLETE`, 48 payload files,
  8 successful RPC responses, 2 retained failed requests, HTTP 403 diagnostic,
  unchanged database bytes, checkpoint retained at 25,919,603.

## Bounded classifications

| Package | Classification | What it establishes | What it does not establish |
| --- | --- | --- | --- |
| Successful recent resume | `OBSERVED_EVENT_ONLY` | One finalized USDC event was retained across ordinary process restart; cursor advanced; replay created no second local event or console notification. | SafeAgent action confirmation, payment intent matching, independent provider completeness, or exactly-once external execution. |
| Failed historical resume | `INCOMPLETE_RETRIEVAL` / `UNCERTAIN` | Both retrieval attempts failed before completion; state and checkpoint remained unchanged. | It cannot be labeled `MISSING_EXTERNALLY`, `CONFIRMED`, or `REJECTED`. |

## Product conclusion

The incomplete control passes the important negative test: failed external
retrieval remains unresolved and is not converted into evidence of absence. The
successful control shows that SafeAgent Control needs an observation-only
ingestion mode for evidence bundles without an originating intent dataset.

This is useful outside validation of a classification boundary. It is not yet
the commercial milestone of finding a previously unknown, actionable operator
discrepancy.
