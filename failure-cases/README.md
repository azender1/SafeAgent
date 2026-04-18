# Failure Case Library

A set of concrete, reproducible failure cases showing how retries, ambiguous completion, and local state assumptions can create duplicate real-world side effects.

## Included cases

1. **Trading Duplicate Execution** — a timeout + retry doubles a QQQ position.
2. **Payment Retry Duplicate** — a charge request times out and replays the same debit.
3. **Notification Duplicate Send** — a delivery retry sends the same user message twice.
4. **State Desync Replay** — local state says "flat" while reality already changed.
5. **PeerPlay Tournament Duplicate Payout** — settlement retry would pay the same winner twice without execution control.

## Why this exists

Most systems treat retry as a safe default.

That is true for computation.
It is not true for irreversible side effects.

Once a system touches trades, payments, bookings, messages, or any external stateful API, a retry can become a second real-world action.

## Demo assets

Primary demo assets already in this repo:

- `assets/safeagent_trading_demo_v2.gif`
- `assets/safeagent_trading_demo_v2.mp4`
- `assets/postgres_demo.gif`
- `deck/SAFEAGENT_DECK_PRO_V2_FINAL.pptx`
- `examples/peerplay_tournament_settlement_demo.py`
- `deck/SAFEAGENT_DECK_PRO_V2_FINAL.pptx`
- `examples/peerplay_tournament_settlement_demo.py`

## Challenge

If you can produce a duplicate irreversible action under the SafeAgent execution model, open an issue with the exact scenario.
