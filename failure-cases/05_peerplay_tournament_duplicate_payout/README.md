# PeerPlay Tournament Duplicate Payout

## Scenario

A PeerPlay tournament finishes and the settlement layer prepares to release funds from the prize pool.

- 16 players join at **$5.00** each
- Gross entry fees: **$80.00**
- Platform rake: **$8.00**
- Winner payout: **$72.00**

The verification or payout call times out before confirmation returns.
The system retries the same logical settlement.

## Failure

Without an execution boundary, the retry can trigger a second payout.

**Effect:**

- Intended winner payout: **$72.00**
- Actual payout after replay: **$144.00**
- Intended rake collection: **$8.00**
- Actual rake after replay: **$16.00**
- Financial state becomes invalid

## Why it happens

The caller cannot prove whether the first settlement already committed.

In that ambiguity window, retry defaults to replay.
Replay becomes a second irreversible money movement.

## Without SafeAgent

```text
[SETTLEMENT] release prize to PLAYER_W456 amount=$72.00
[SETTLEMENT] release rake amount=$8.00
[ERROR] timeout waiting for settlement confirmation
[RETRY] same tournament settlement retried
[SETTLEMENT] release prize to PLAYER_W456 amount=$72.00   <-- DUPLICATE
[SETTLEMENT] release rake amount=$8.00                   <-- DUPLICATE
```

## With SafeAgent

```text
[SETTLEMENT] release prize to PLAYER_W456 amount=$72.00
[SETTLEMENT] release rake amount=$8.00
[ERROR] timeout waiting for settlement confirmation
[RETRY] same tournament settlement retried
[SAFEAGENT] request_id already exists
[SAFEAGENT] returning cached result
```

## Root cause

No execution boundary between tournament result verification and irreversible payout side effects.

## SafeAgent outcome

- duplicate payout blocked
- duplicate rake blocked
- prior receipt returned deterministically
- tournament settlement remains financially correct

## Demo

Run the working reference example:

```text
examples/peerplay_tournament_settlement_demo.py
```
