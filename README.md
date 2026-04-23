# SafeAgent

<p align="center">
  <img src="assets/safeagent_trading_demo_v2.gif" alt="Trading Demo" width="960">
</p>

> Deterministic execution for real-world actions under uncertainty.

---

## The Problem

Agents don’t fail on decisions.  
They fail on **uncertain completion**.

```text
submit order
timeout
retry
submit order   <-- duplicate
```

---

## Real Impact

```text
Position: 2 → 4 shares
Capital: $710.20 → $1420.40
```

Not bad logic.  
Just no execution boundary.

---

## The Fix

SafeAgent enforces:

- request identity
- execution boundary
- deterministic retry resolution

```text
timeout
retry
SafeAgent: returning cached result
```

---

## Core Idea

```text
Agent → SafeAgent → Real World
```

Retries don’t replay.  
They **resolve**.

---

## PeerPlay Settlement Use Case

SafeAgent is also the settlement guard for PeerPlay-style tournament payout flows.

- tournament result verified
- payout request sent
- confirmation lost / timeout
- retry fires

Without an execution boundary, the same winner can be paid twice.
With SafeAgent, the retry resolves against the prior request record and returns the original result.

Start here:

- `examples/peerplay_tournament_settlement_demo.py`
- `failure-cases/05_peerplay_tournament_duplicate_payout/README.md`

---

## Failure Cases

- Trading → double position
- Payments → duplicate charge
- Notifications → duplicate send
- State desync → unintended re-entry
- PeerPlay → duplicate tournament payout

See: `failure-cases/`

---

## Working Demos

- Trading retry demo: `assets/safeagent_trading_demo_v2.gif`
- Postgres runtime proof: `assets/postgres_demo.gif`
- PeerPlay tournament settlement: `examples/peerplay_tournament_settlement_demo.py`
- Deck: `deck/SAFEAGENT_DECK_PRO_V2_FINAL.pptx`

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>Before / After</h3>
      <img src="assets/before_after_demo.gif" alt="Before After Demo" width="100%">
    </td>
    <td width="50%" valign="top">
      <h3>Execution Guard</h3>
      <img src="assets/execution_guard_demo.gif" alt="Execution Guard Demo" width="100%">
    </td>
  </tr>
</table>

<details>
  <summary><strong>Show Postgres runtime proof</strong></summary>
  <br>
  <img src="assets/postgres_demo.gif" alt="Postgres Demo" width="960">
</details>

### Full Video
[Download MP4](https://github.com/azender1/SafeAgent/raw/main/assets/safeagent_trading_demo_v2.mp4)

---

## Status

Reference implementation for handling duplicate execution under uncertain completion.

---

## Repo

[github.com/azender1/SafeAgent](https://github.com/azender1/SafeAgent)

---

## Need this fixed in your system?

I offer a **Duplicate Execution Risk Audit**.

I will:
- Identify where retries + timeouts can cause duplicate execution (payments, trades, workflows, API calls)
- Reproduce the failure cases
- Provide the exact fix pattern (with SafeAgent or equivalent design)

**Flat fee: $499**  
(includes report + concrete fix recommendations)

If you want a pilot integration or full production implementation, we can scope that after.

DM me on X (@ZGT512666) or open an issue with "AUDIT".
