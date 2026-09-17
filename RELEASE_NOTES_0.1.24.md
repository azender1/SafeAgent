# SafeAgent 0.1.24 — fail closed on unresolved PENDING claims

## Safety correction

Earlier releases deleted a `PENDING` claim after its configured TTL. That made
the logical action claimable again even though elapsed time did not establish
whether the external provider had accepted the first attempt.

Version 0.1.24 changes the default recovery behavior:

- stale `PENDING` claims remain `PENDING`;
- the same logical request ID remains blocked;
- `/sweep` reports stale unresolved claims but changes no claim state;
- callers must reconcile against provider records or rely on provider-native
  idempotency before authorizing recovery;
- SQLite and PostgreSQL implement the same fail-closed behavior.

## API response

`POST /sweep` now returns:

```json
{
  "swept": 0,
  "stale_pending": 1,
  "requires_reconciliation": true,
  "action": "none"
}
```

The `swept` field remains for compatibility and is always zero.

## Guarantee boundary

SafeAgent proves durable local claim state. It does not infer the external
outcome from a local `PENDING` row, a timeout, or elapsed time. End-to-end
outcome certainty requires authoritative provider evidence.
