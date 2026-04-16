# Trading Duplicate Execution

## Scenario

A trading bot submits a market order to buy QQQ exposure.
The broker or transport layer times out before confirmation returns.
The system retries.

## Failure

The retry submits the same order again.

**Effect:**

- Intended position: **2 shares**
- Actual position after replay: **4 shares**
- Intended capital: **$710.20**
- Actual capital after replay: **$1,420.40**
- Unintended extra exposure: **$710.20**

## Why it happens

The caller cannot prove whether the first request committed.

In that ambiguity window, retry defaults to replay.
Replay becomes a second side effect.

## Without SafeAgent

```text
[09:45:02] SUBMIT ORDER: BUY QQQ 2 @ 355.10
[09:45:03] Broker response pending...
[09:45:05] ERROR: timeout waiting for confirmation
[09:45:05] Retrying request...
[09:45:06] SUBMIT ORDER: BUY QQQ 2 @ 355.10   <-- DUPLICATE
[09:45:07] Position: 2 -> 4 shares
[09:45:07] Capital: $710.20 -> $1,420.40
```

## With SafeAgent

```text
[09:45:02] SUBMIT ORDER: BUY QQQ 2 @ 355.10
[09:45:03] Broker response pending...
[09:45:05] ERROR: timeout waiting for confirmation
[09:45:05] Retrying request...
[09:45:05] SafeAgent: request_id already exists
[09:45:05] SafeAgent: returning cached result
[09:45:06] Position: 2 shares
[09:45:06] Capital: $710.20
```

## Root cause

No execution boundary between agent intent and irreversible market side effects.

## SafeAgent outcome

- duplicate replay blocked
- original request resolved deterministically
- exactly-once caller-side behavior enforced

## Assets

Use these existing repo assets:

- `assets/safeagent_trading_demo_v2.gif`
- `assets/safeagent_trading_demo_v2.mp4`
