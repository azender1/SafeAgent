# SafeAgent Launch Plan

## Primary Assets Already In Repo

- `assets/safeagent_trading_demo_v2.gif`
- `assets/safeagent_trading_demo_v2.mp4`
- `assets/postgres_demo.gif`
- `deck/SAFEAGENT_DECK_PRO_V2_FINAL.pptx`
- `assets/SAFEAGENT_DECK_PRO_V2_FINAL (1).mp4`
- `examples/peerplay_tournament_settlement_demo.py`

## Recommended X Posting Sequence

### Post 1 — trading proof

Broker timeout.

Retry fires.

Without a guard, the same trade can replay.

Real-money systems fail on uncertain completion, not just bad signals.

SafeAgent:
https://github.com/azender1/SafeAgent

Asset:
- `assets/safeagent_trading_demo_v2.gif`

### Post 2 — core wedge

Idempotency ≠ correctness.

The real question isn’t just:
"did this happen?"

It’s:
"should this still happen now?"

https://github.com/azender1/SafeAgent

Asset:
- Clip A from deck (00:50.83 → 01:04.63)

### Post 3 — product explanation

Same request.
Same retry.
Different result.

That’s the shortest explanation of SafeAgent.

https://github.com/azender1/SafeAgent

Asset:
- Clip B from deck (01:22.77 → 01:37.77)

### Post 4 — runtime proof

Runtime proof:

first attempt executes  
retry detects duplicate  
cached result returned  
no replay

https://github.com/azender1/SafeAgent

Asset:
- Clip C from deck (01:37.77 → 01:51.90)

### Post 5 — PeerPlay tournament settlement

Tournament payout hits a timeout.

Retry fires.

Without a guard: payout executes twice.
With SafeAgent: duplicate detected, result returned.

This is how you prevent duplicate money movement under uncertainty.

https://github.com/azender1/SafeAgent

Asset:
- terminal recording of `examples/peerplay_tournament_settlement_demo.py`
- or screenshot of the PASS summary

## GitHub / HN / Reddit placement

### Hacker News title
Show HN: Preventing duplicate actions from retries in AI agent systems

### Agent-ledger GitHub reply
Added a small failure-cases library with reproducible scenarios for the ambiguous completion window:

https://github.com/azender1/SafeAgent/tree/main/failure-cases

Also added a PeerPlay-style tournament settlement case showing how retries can duplicate payout without an execution boundary.

Curious how closely these cases match what others are seeing in production.

### Microsoft Agent Framework reply
This is exactly the failure mode I’ve been hitting.

Short clip showing retry → replay vs retry → reconcile in practice:
[link to X post]

In your example, side effects have already committed before the checkpoint boundary, but retry has no way to distinguish “continue unresolved work” from “replay what already happened.”

That’s where retry turns into replay.

## Run Commands

### PeerPlay tournament settlement demo
```bash
cd SAFEAGENT
python examples/peerplay_tournament_settlement_demo.py
```

### Trading / Postgres examples
```bash
cd SAFEAGENT
python examples/postgres_demo.py
```
