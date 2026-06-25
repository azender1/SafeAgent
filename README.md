# SafeAgent — Execution Guard for AI Agents
![SafeAgent — Exactly-Once Execution for AI Agents](assets/HERO%20IMAGE2%20JUN%2017%2C%202026%2C%2010_41_10%20PM.png)
<!-- mcp-name: io.github.azender1/safeagent -->

`POST /claim` · [safeagent-production.up.railway.app](https://safeagent-production.up.railway.app)  
Dashboard: [safeagent-dashboard-2.vercel.app](https://safeagent-dashboard-2.vercel.app)

```bash
# Claim an action — free, no payment header required
curl -s -X POST https://safeagent-production.up.railway.app/claim \
  -H "Content-Type: application/json" \
  -d '{"request_id":"order:TQQQ:buy:6:2026-05-19T13:31:00-04:00","action":"order"}'
# → {"status":"PROCEED","request_id":"..."}

# Settle after the action fires
curl -s -X POST https://safeagent-production.up.railway.app/settle/order:TQQQ:buy:6:2026-05-19T13:31:00-04:00 \
  -H "Content-Type: application/json" \
  -d '{"result":{"status":"completed"}}'
# → {"status":"committed","request_id":"..."}

# Retry with the same request_id — returns SKIP
curl -s -X POST https://safeagent-production.up.railway.app/claim \
  -H "Content-Type: application/json" \
  -d '{"request_id":"order:TQQQ:buy:6:2026-05-19T13:31:00-04:00","action":"order"}'
# → {"status":"SKIP","request_id":"...","existing":{...}}

# Free test endpoint — same logic, limited to 10 calls per IP
curl -s -X POST https://safeagent-production.up.railway.app/claim/test \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"bot-1","action_type":"order","scope":"TQQQ:buy:bar:2026-05-19T13:31:00-04:00"}'
# → {"status":"PROCEED","request_id":"...","test":true,"calls_remaining":9}
```

Indexed on [Bazaar](https://orbisapi.com/proxy/safeagent-execution-guard-bb0b02).

---

## Verified on Soma

SafeAgent is the first verified external integrator on [Soma](https://soma-api.rgiskard.xyz/catalog) — the Mycelium agent catalog. Every production execution is anchored on-chain via Mycelium Trails and independently verifiable without going through the operator.

- **Integrator badge** — verified external client running Mycelium Trails in production
- **Live trails** — [https://argentum-api.rgiskard.xyz/dashboard/trails?client=safeagent-prod](https://argentum-api.rgiskard.xyz/dashboard/trails?client=safeagent-prod)
- **Public audit** — any auditor can verify SafeAgent executions independently

---

## What it does

SafeAgent is an exactly-once execution guard. It prevents AI agents and SaaS applications from firing the same action twice — on crash-retry, duplicate signal, webhook replay, or concurrent execution across multiple instances.

Every action gets a stable `request_id` derived from what the agent is doing and when. The first call commits. Every subsequent call with the same key returns `SKIP` and the original result. No double charges. No double emails. No double orders. No duplicate webhooks.

**State machine:** `PENDING → COMMITTED | SKIP`

**Common failure modes SafeAgent prevents:**

| Scenario | Without SafeAgent | With SafeAgent |
|---|---|---|
| Stripe charge times out, retry fires | Customer charged twice | Second charge returns SKIP |
| Welcome email on signup retried | User gets two welcome emails | Second send returns SKIP |
| Webhook delivered twice (Stripe/GitHub/Twilio guarantee at-least-once) | Event processed twice | Second processing returns SKIP |
| Workspace provisioned on retry | Two workspaces created | Second provision returns SKIP |
| AI agent tool call retried after crash | Duplicate side effect | Second call returns SKIP |

---

## The stack

SafeAgent is the exactly-once enforcement layer in a formally specified agent execution integrity stack:

```
AgentGraph safety verdict (pre-execution safety gate)
    └── SafeAgent (exactly-once execution guard)  ← you are here
        └── Nobulex (signed bilateral receipt)
            └── Mycelium Trails (on-chain anchor)
```

Each layer is independently authored and independently verifiable. None trusts the others.

**EU AI Act Art. 12** — enforcement deadline August 2, 2026. SafeAgent provides tamper-evident execution logs for agent compliance. The full stack satisfies "tamper-evident" independently of platform logs.

---

## API

### POST /claim

Gate an action. Returns PROCEED on first call, SKIP on any repeat.

**Request body:**
```json
{
  "request_id": "order:TQQQ:buy:6:2026-05-19T13:31:00-04:00",
  "action": "order"
}
```

- `request_id` — stable identifier derived from what the agent is doing. Same inputs = same key.
- `action` — the action type being gated (e.g. `payment.send`, `email`, `trade`, `order`)

**Response:**
```json
{ "status": "PROCEED", "request_id": "order:TQQQ:buy:6:2026-05-19T13:31:00-04:00" }
```

Retry with the same payload:
```json
{ "status": "SKIP", "request_id": "order:TQQQ:buy:6:2026-05-19T13:31:00-04:00", "existing": {...} }
```

### POST /settle/{request_id}

Settle a claim after the action fires. Advances status from PENDING to COMMITTED.

```bash
curl -s -X POST https://safeagent-production.up.railway.app/settle/order:TQQQ:buy:6:2026-05-19T13:31:00-04:00 \
  -H "Content-Type: application/json" \
  -d '{"result":{"status":"completed","txid":"abc123"}}'
```

```json
{ "status": "committed", "request_id": "..." }
```

### POST /claim/test

Free test endpoint — same logic, no payment required. Limited to 10 calls per IP total.

```bash
curl -s -X POST https://safeagent-production.up.railway.app/claim/test \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"bot-1","action_type":"order","scope":"TQQQ:buy:6:bar:2026-05-19T13:31:00-04:00"}'
```

```json
{ "status": "PROCEED", "request_id": "...", "test": true, "calls_remaining": 9 }
```

### GET /audit

Full claim history. Filter by `agent_id`, `action`, `status`, or timestamp range.

```bash
curl "https://safeagent-production.up.railway.app/audit?agent_id=bot-1&status=COMMITTED"
```

```json
{"items": [...], "total": 5, "limit": 100, "offset": 0}
```

Parameters: `agent_id`, `action`, `status`, `from_ts`, `to_ts`, `limit` (max 1000), `offset`.

---

## Integration

### Python

```python
import requests

def claim(request_id: str, action: str) -> dict:
    r = requests.post(
        "https://safeagent-production.up.railway.app/claim",
        headers={"Content-Type": "application/json"},
        json={"request_id": request_id, "action": action}
    )
    return r.json()  # {"status": "PROCEED"|"SKIP", "request_id": "..."}

def settle(request_id: str, result: dict) -> dict:
    r = requests.post(
        f"https://safeagent-production.up.railway.app/settle/{request_id}",
        headers={"Content-Type": "application/json"},
        json={"result": result}
    )
    return r.json()

# Usage
response = claim("payment:customer-123:invoice-456", "payment.send")
if response["status"] == "PROCEED":
    result = send_payment(...)
    settle("payment:customer-123:invoice-456", {"status": "completed"})
elif response["status"] == "SKIP":
    print("Already executed, skipping")
```

### SaaS (Stripe / email / webhooks)

```python
import requests

def safe_stripe_charge(customer_id, amount, idempotency_key):
    request_id = f"stripe:charge:{customer_id}:{idempotency_key}"

    response = requests.post(
        "https://safeagent-production.up.railway.app/claim",
        headers={"Content-Type": "application/json"},
        json={"request_id": request_id, "action": "stripe_charge"}
    ).json()

    if response["status"] == "SKIP":
        return response.get("existing")

    charge = stripe.PaymentIntent.create(amount=amount, customer=customer_id)
    requests.post(
        f"https://safeagent-production.up.railway.app/settle/{request_id}",
        headers={"Content-Type": "application/json"},
        json={"result": {"charge_id": charge.id}}
    )
    return charge
```

**Webhook deduplication** — Stripe, GitHub, and Twilio all guarantee at-least-once delivery. SafeAgent turns at-least-once into exactly-once:

```python
def handle_stripe_webhook(event):
    response = requests.post(
        "https://safeagent-production.up.railway.app/claim",
        headers={"Content-Type": "application/json"},
        json={"request_id": event["id"], "action": "stripe_event"}
    ).json()

    if response["status"] == "SKIP":
        return {"ok": True}

    provision_subscription(event)
    requests.post(
        f"https://safeagent-production.up.railway.app/settle/{event['id']}",
        headers={"Content-Type": "application/json"},
        json={"result": {"processed": True}}
    )
```

### CrewAI

```python
from crewai import Agent
import sqlite3

guard_con = sqlite3.connect("safeagent_orders.db", check_same_thread=False)
# ... same guard pattern, keyed on tool_name + input hash + session_id
```

PR [crewAIInc/crewAI#5822](https://github.com/crewAIInc/crewAI/pull/5822) adds pluggable idempotency backends. SafeAgent's SQLite schema is compatible.

### WisePick

[WisePick](https://github.com/w2jmoe/WisePick) is a deterministic routing layer for agent runtimes. The integration splits **capability selection** from **durable, idempotent execution** — WisePick answers *what* and *which provider*, SafeAgent answers *whether this logical work already ran*.

**Stack:**
```
WisePick /v1/decide → DashClaw → SafeAgent → Mycelium Trails → Base/Arbitrum
```

- Adapter: [`adapters/safeagent_adapter.py`](https://github.com/w2jmoe/WisePick/blob/main/adapters/safeagent_adapter.py)
- Integration docs: [`docs/integrations/safeagent.md`](https://github.com/w2jmoe/WisePick/blob/main/docs/integrations/safeagent.md)
- Integration thread: [SafeAgent #9](https://github.com/azender1/SafeAgent/issues/9)

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

---

## Case study: crash-retry duplicate prevention

A trading bot fires a market order to buy 6 shares of TQQQ. The broker accepts it. The bot crashes before updating state. On restart — same signal, same bar — the bot fires again. The broker fills it twice. The agent now holds 12 shares when it intended to hold 6.

This is not theoretical. It happens on any unhandled exception between order submission and state persistence.

SafeAgent derives a stable key before touching the broker:
```
request_id = "order:TQQQ:buy:6:2026-05-19T13:31:00-04:00"
```

First call: PROCEED. Side effect fires. Settle writes COMMITTED.  
Crash and retry: SKIP. Broker never touched again.

---

## Case study: live duplicate blocking — May 21, 2026

Six confirmed SKIP events from a live session on the full stack: DashClaw, SafeAgent, Mycelium Trails, Base/Arbitrum, broker Alpaca.

- 0942 ET: duplicate buy TQQQ qty=6 blocked, $452
- 0947 ET: duplicate add TQQQ qty=6 blocked, $452
- 0949 ET: duplicate sell TQQQ qty=12 on flip, $902
- 1000 ET: duplicate entry TQQQ qty=6 blocked, $454
- 1014 ET: duplicate sell TQQQ qty=18 on V20 flip, $1,350
- 1106 ET: duplicate SQQQ add during scale-in, $43

Full session data: https://gist.github.com/azender1/b9112b6519c935df4a75cb05cd250e26

---

## Conformance

SafeAgent participates in the A2A cross-implementation conformance corpus. Byte-verified fixtures across four independent implementations:

- kenneives (agentgraph) — verifier-attestation-v0: 30/30 ✓
- evidai (LemonCake) — gated-preflight-v1: 33/33 ✓
- haroldmalikfrimpong-ops (agentid) — independent verifier ✓
- SafeAgent — exactly-once-v1.1 ✓

A2A #1920 closed. All four implementations satisfy freshness and replay requirements.

---

## Canonical `action_ref` derivation

JCS (RFC 8785), aligned with argentum-core, Nobulex, and APS:

```python
import hashlib
import rfc8785

def compute_action_ref(
    agent_id: str,
    action_type: str,
    scope: str,
    timestamp: str,  # RFC 3339 UTC, 3-digit ms: "2026-05-15T10:00:00.123Z"
) -> str:
    payload = {
        "agent_id": agent_id,
        "action_type": action_type,
        "scope": scope,
        "timestamp": timestamp,
    }
    canonical = rfc8785.dumps(payload)
    return hashlib.sha256(canonical).hexdigest()
```

`timestamp` is an RFC 3339 UTC string with exactly 3 millisecond digits. The trailing `Z` is mandatory.

Conformance fixtures: [giskard09/argentum-core tag action-ref-v1.0](https://github.com/giskard09/argentum-core)

---

## Deployment

Railway · Serverless OFF · always-on  
PyPI: `pip install safeagent-exec-guard`  
npm: `npm install n8n-nodes-safeagent`  
MCP Registry: `io.github.azender1/safeagent`  
Dashboard: [safeagent-dashboard-2.vercel.app](https://safeagent-dashboard-2.vercel.app)

---

## License

Apache-2.0

<!-- mcp-name: io.github.azender1/safeagent -->
