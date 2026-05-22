# SafeAgent — Execution Guard for AI Agents

**Pay per claim via x402.**  
`POST /claim` · `$0.001` · Base + Solana · [safeagent-production.up.railway.app](https://safeagent-production.up.railway.app)
x-payment: <Base or Solana payment>
POST /claim
{ "agent_id": "...", "action_type": "order", "scope": "TQQQ:buy:bar:2026-05-19T13:31:00-04:00" }
→ { "status": "COMMITTED" | "SKIP", "request_id": "..." }

Indexed on [Bazaar](https://orbisapi.com/proxy/safeagent-execution-guard-bb0b02). 102 requests / 7 days.

---

## What it does

SafeAgent is an exactly-once execution guard. It prevents AI agents from firing the same action twice — on crash-retry, duplicate signal, or concurrent execution across multiple instances.

Every action gets a stable `request_id` derived from what the agent is doing and when. The first call commits. Every subsequent call with the same key returns `SKIP` and the original result. No double orders. No double tool calls. No double payments.

**State machine:** `PENDING → COMMITTED | SKIP`

---

## x402 API

### POST /claim

Gate an action. Returns COMMITTED on first call, SKIP on any repeat.

```bash
curl -X POST https://safeagent-production.up.railway.app/claim \
  -H "Content-Type: application/json" \
  -H "x-payment: <payment>" \
  -d '{
    "agent_id": "bot-1",
    "action_type": "order",
    "scope": "TQQQ:buy:6:bar:2026-05-19T13:31:00-04:00"
  }'
```

```json
{ "status": "COMMITTED", "request_id": "a3f9..." }
```

Retry with the same payload:

```json
{ "status": "SKIP", "request_id": "a3f9...", "cached_result": "..." }
```

### GET /audit *(coming soon — gated behind x402)*

Full claim history for an agent_id.

---

## Local guard (embedded SQLite)

The same guarantee without the network call. Drop this into any Python agent:

```python
import sqlite3

_SA_DB = "safeagent_orders.db"
_sa_con = sqlite3.connect(_SA_DB, check_same_thread=False)
_sa_con.execute("""CREATE TABLE IF NOT EXISTS orders (
    request_id TEXT PRIMARY KEY,
    result     TEXT,
    status     TEXT DEFAULT 'PENDING',
    created_at TEXT DEFAULT (datetime('now'))
)""")
_sa_con.commit()

def place_order_with_guard(symbol, qty, side, bar_ts):
    request_id = f"order:{symbol}:{side}:{qty}:{bar_ts}"

    _sa_con.execute(
        "INSERT OR IGNORE INTO orders (request_id, status) VALUES (?, 'PENDING')",
        (request_id,)
    )
    _sa_con.commit()

    row = _sa_con.execute(
        "SELECT status, result FROM orders WHERE request_id = ?",
        (request_id,)
    ).fetchone()

    if row and row[0] == 'COMMITTED':
        print(f"SAFEAGENT SKIP: {request_id}")
        return row[1]

    result = place_order(symbol, qty, side)

    _sa_con.execute(
        "UPDATE orders SET status='COMMITTED', result=? WHERE request_id=?",
        (json.dumps(str(result)), request_id)
    )
    _sa_con.commit()
    return result
```

The `request_id` is stable: same symbol, same side, same quantity, same bar timestamp = same key. If the bot crashes between firing and settling, the next run sees `PENDING`, re-fires, and settles. If it crashed after settling, the next run sees `COMMITTED` and returns SKIP.

---

## Case study: crash-retry duplicate prevention

**What actually happens without a guard.**

A trading bot fires a market order to buy 6 shares of TQQQ. The broker accepts it. The bot crashes before updating state. On restart — same signal, same bar — the bot fires again. The broker fills it twice. The agent now holds 12 shares when it intended to hold 6.

This is not theoretical. It happens on any unhandled exception between order submission and state persistence.

**How SafeAgent blocks it.**

The guard derives a stable key from the order parameters before touching the broker:
request_id = f"order:{symbol}:{side}:{qty}:{bar_ts}"
e.g. "order:TQQQ:buy:6:2026-05-19T13:31:00-04:00"

Then:

1. `INSERT OR IGNORE` — atomic, no-op if the key already exists
2. Check status — if `COMMITTED`, return cached result immediately
3. Fire order — only reaches the broker if step 2 passed
4. Settle — write `COMMITTED` + broker response

On crash between steps 3 and 4: key is `PENDING`. Next run re-fires. This is safe — `PENDING` means the order may or may not have landed. The broker's own idempotency (duplicate `client_order_id`) handles the edge case.

On crash after step 4: key is `COMMITTED`. Next run hits step 2, logs `SAFEAGENT SKIP`, returns the original order. The broker is never touched again.

**Live proof from May 19 session.**

`safeagent_orders.db` — 23 orders, 23 COMMITTED, 0 PENDING.
order:TQQQ:buy:6:2026-05-19T13:31:00-04:00          COMMITTED
order:TQQQ:sell:18:2026-05-19T13:25:00-04:00:TRAIL   COMMITTED
order:SQQQ:buy:11:2026-05-19T13:54:00-04:00          COMMITTED
order:SQQQ:sell:22:2026-05-19T14:00:00-04:00:V20     COMMITTED
order:TQQQ:buy:6:2026-05-19T14:02:00-04:00           COMMITTED
order:TQQQ:buy:6:2026-05-19T14:05:00-04:00           COMMITTED
order:TQQQ:sell:12:2026-05-19T14:18:00-04:00:V20     COMMITTED
order:SQQQ:buy:11:2026-05-19T14:20:00-04:00          COMMITTED
order:SQQQ:sell:11:2026-05-19T14:26:00-04:00:V20     COMMITTED
order:TQQQ:buy:6:2026-05-19T14:26:00-04:00           COMMITTED
order:TQQQ:sell:6:2026-05-19T14:31:00-04:00:FLIP     COMMITTED
order:SQQQ:buy:11:2026-05-19T14:31:00-04:00          COMMITTED
order:SQQQ:buy:11:2026-05-19T14:42:00-04:00          COMMITTED
order:SQQQ:buy:11:2026-05-19T14:53:00-04:00          COMMITTED
order:SQQQ:sell:33:2026-05-19T15:00:00-04:00:TRAIL   COMMITTED
order:SQQQ:buy:11:2026-05-19T15:03:00-04:00          COMMITTED
order:SQQQ:sell:11:2026-05-19T15:10:00-04:00:V20     COMMITTED
order:TQQQ:buy:6:2026-05-19T15:14:00-04:00           COMMITTED
order:TQQQ:sell:12:2026-05-19T15:20:00-04:00:V20     COMMITTED
... (23 total)

Every order that fired is in the db as COMMITTED. If either bot instance had crashed mid-flight and restarted with the same signal, it would have hit the COMMITTED row and stopped. The broker would never have seen a duplicate submission.

**The two-bot scenario.**

Two instances of the same bot ran against the same shared `safeagent_orders.db`. They operated on different timelines — bot 1 entered the morning bull wave at 12:32, bot 2 was blocked by the broker's open-position check and entered later at 13:31. They never tried to fire the same `request_id` because they were acting on different bars.

The scenario where the db guard fires instead of the broker check: two bots on separate broker accounts, both wired to the same `safeagent_orders.db`, both reading the same bar signal at the same second. Bot 1 fires `INSERT OR IGNORE` and wins the atomic write. Bot 2 fires the same insert — SQLite's `INSERT OR IGNORE` drops it silently. Bot 2 reads the row, sees `PENDING`, and proceeds to fire. Both orders land.

This is the gap. `INSERT OR IGNORE` + status check handles crash-retry cleanly. For true concurrent multi-agent deduplication, the status check needs to happen inside a transaction with a row-level lock, or the guard needs to be the hosted endpoint where the write is serialized server-side.

The hosted `/claim` endpoint is that serialization layer.

---

## Case study: live duplicate blocking — May 21, 2026

Six confirmed SKIP events from a live session on the full stack: DashClaw, SafeAgent, Mycelium Trails, Base/Arbitrum, broker Alpaca.

- 0942 ET: duplicate buy TQQQ qty=6 blocked, $452
- 0947 ET: duplicate add TQQQ qty=6 blocked, $452
- 0949 ET: duplicate sell TQQQ qty=12 on flip, $902
- 1000 ET: duplicate entry TQQQ qty=6 blocked, $454
- 1014 ET: duplicate sell TQQQ qty=18 on V20 flip, $1,350
- 1106 ET: duplicate SQQQ add during scale-in, $43

**Gap surfaced: exit side has no guard.**

At 1114 ET a legitimate SQQQ exit failed with 422 Unprocessable Entity after three retries. Broker API continued reporting an open TQQQ position. Bot logged ENTRY BLOCKED from 1133 through 1400 — three hours of blocked entries from a phantom position. Those blocks appear in any receipt chain as legitimate decisions with no trace of the upstream failure.

Exit-side exactly-once semantics are the next spec item.

Full session data: https://gist.github.com/azender1/b9112b6519c935df4a75cb05cd250e26

---

## Integration

### Python (local db)

Copy the guard block from the source above into `place_order_with_retry`. Works with any broker. No network dependency.

### CrewAI

```python
from crewai import Agent
import sqlite3

guard_con = sqlite3.connect("safeagent_orders.db", check_same_thread=False)
# ... same guard pattern, keyed on tool_name + input hash + session_id
```

PR [crewAIInc/crewAI#5822](https://github.com/crewAIInc/crewAI/pull/5822) adds pluggable idempotency backends. SafeAgent's SQLite schema is compatible.

### x402 (hosted)

```python
import requests

def claim(agent_id, action_type, scope, payment_header):
    r = requests.post(
        "https://safeagent-production.up.railway.app/claim",
        headers={"x-payment": payment_header, "Content-Type": "application/json"},
        json={"agent_id": agent_id, "action_type": action_type, "scope": scope}
    )
    return r.json()  # {"status": "COMMITTED"|"SKIP", "request_id": "..."}
```

---

## Stack
DashClaw (attribution + approval) → decision_id
└── SafeAgent (exactly-once guard) → request_id
└── Mycelium Trails (on-chain receipt) → action_ref → Base/Arbitrum

Canonical action_ref derivation (aligned with APS, Nobulex, argentum-core):

```python
import hashlib, struct

action_ref = hashlib.sha256(
    agent_id.encode('utf-8') +
    action_type.encode('utf-8') +
    scope.encode('utf-8') +
    struct.pack('>Q', timestamp_ms)
).hexdigest()
```

---

## Deployment

Railway · Serverless OFF · always-on  
PyPI: `pip install safeagent-exec-guard`  
npm: `npm install n8n-nodes-safeagent`  
MCP Registry: `io.github.azender1/safeagent`

---

## License

Apache-2.0
