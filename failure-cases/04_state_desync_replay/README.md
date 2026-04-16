# State Desync Replay

## Scenario

A local bot process stores state such as `entered=false` or `trade_count=0`.
The external world may already have changed, but local memory or disk state is stale, missing, or reset.
A restart occurs.

## Failure

The system believes no side effect has happened and re-enters the same action.

**Effect:**

- duplicate position entry
- duplicated workflow step
- inconsistent audit trail

## Example

```text
Local state: entered=false
Reality: order already filled
Process restarts
System decides it is safe to enter again
```

## Why it happens

Local state is not the same as execution truth.
A restart or desync can erase the caller's memory while the external side effect still exists.

## With SafeAgent

SafeAgent ties execution to stable request identity and durable prior outcomes, not just transient process state.

## Principle

If a system only checks local memory before replaying, it is one restart away from duplicate behavior.
