# n8n-nodes-safeagent

An [n8n](https://n8n.io) community node that wraps the
[`safeagent-exec-guard`](https://pypi.org/project/safeagent-exec-guard/) Python library to bring
the **claim-before-execute** idempotency pattern to your n8n workflows.

---

## The Claim-Before-Execute Pattern

AI agents and event-driven workflows face a hard problem: the same logical request can arrive more
than once (webhook retries, queue redeliveries, user double-clicks). Without a guard, every
duplicate triggers the side effect again — double charges, duplicate emails, duplicate DB rows.

**Claim-before-execute** solves this with a single atomic database write:

```
1. Before doing anything irreversible, claim the (requestId, actionName) pair.
2. If the claim succeeds  → you are the first runner.  Proceed with the action.
3. If the claim is denied → someone else already ran it.  Return the cached receipt and stop.
```

The claim is implemented as a SQL `INSERT … ON CONFLICT DO NOTHING` (or equivalent), making it
naturally atomic and race-condition-safe even under concurrent workers.

```
Incoming event
     │
     ▼
┌────────────────────┐
│  SafeAgent Guard   │  ← this n8n node
│  insert_if_not_    │
│  exists(rid, act)  │
└────────┬───────────┘
         │
   ┌─────┴──────┐
   │            │
inserted=true  inserted=false
   │            │
   ▼            ▼
Proceed     Return cached
with        receipt → skip
action      downstream nodes
```

---

## Installation

### Prerequisites

- n8n ≥ 0.190.0
- Python 3.9+ in `PATH`
- The `safeagent-exec-guard` Python package:

```bash
pip install safeagent-exec-guard
# or, for Postgres support:
pip install "safeagent-exec-guard[postgres]"
```

### Add to n8n

```bash
# In your n8n data directory
npm install n8n-nodes-safeagent
```

Or use the n8n **Settings → Community Nodes → Install** UI and enter `n8n-nodes-safeagent`.

---

## Node Fields

| Field | Type | Description |
|---|---|---|
| **Request ID** | String | Unique identifier for the logical request (e.g. webhook event ID, message UUID). Supports n8n expressions like `{{ $json["id"] }}`. |
| **Action Name** | String | Name of the side-effectful action being guarded (e.g. `send_invoice`, `charge_card`). Together with Request ID it forms the idempotency key. |
| **Backend** | Dropdown | `SQLite (local file)` — zero-config, good for dev / single-instance. `PostgreSQL` — distributed-safe, recommended for production / multi-worker. |
| **SQLite Database Path** | String *(SQLite only)* | Path to the `.db` file. Defaults to `safeagent.db` in the n8n working directory. |
| **Postgres Credentials** | Credential *(Postgres only)* | Host, port, database, user, password, SSL toggle. |

---

## Output Fields

The node adds the following fields to the item's JSON:

| Field | Type | Meaning |
|---|---|---|
| `requestId` | string | Echo of the input Request ID |
| `actionName` | string | Echo of the input Action Name |
| `backend` | string | `"sqlite"` or `"postgres"` |
| `isDuplicate` | boolean | `true` if this (requestId, actionName) was already claimed |
| `shouldProceed` | boolean | `true` if this is a new claim and the action should run |
| `inserted` | boolean | Raw value from the guard library |
| `receipt` | object | The stored execution record (timestamps, metadata, etc.) |

---

## Usage in a Workflow

### Pattern A — IF branch

```
Webhook → SafeAgent Guard → IF (shouldProceed == true)
                                   ├─ true  → Charge Card → Mark Complete
                                   └─ false → Return Cached Receipt
```

### Pattern B — Stop-and-error on duplicate

Add an **IF** node after the guard:

- **Condition**: `{{ $json.isDuplicate }}` equals `true`
- **True branch**: Stop And Error (or Respond to Webhook with the cached receipt)
- **False branch**: continue with the real work

### Minimal inline example

```json
{
  "nodes": [
    {
      "type": "n8n-nodes-safeagent.safeAgent",
      "parameters": {
        "requestId": "={{ $json[\"webhookEventId\"] }}",
        "actionName": "send_welcome_email",
        "backend": "sqlite",
        "sqlitePath": "/data/safeagent.db"
      }
    }
  ]
}
```

---

## Postgres Setup

1. Create a dedicated database and user:

```sql
CREATE DATABASE safeagent;
CREATE USER safeagent_user WITH PASSWORD 'secret';
GRANT ALL PRIVILEGES ON DATABASE safeagent TO safeagent_user;
```

2. The guard library creates the `execution_claims` table automatically on first run (`init_db()`).

3. In n8n, add a **SafeAgent Postgres Credentials** credential and select it in the node.

For high-throughput use-cases add an index:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_claims
  ON execution_claims (request_id, action_name);
```

---

## How the Subprocess Works

The node calls the guard library via `python3 -c "..."` so that:

- No additional n8n-side dependencies are needed beyond Node.js.
- The Python environment (virtualenv, system packages, etc.) is fully under your control.
- The library can be upgraded independently of the n8n node.

The script follows this pattern:

```python
import json
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

store = SQLiteExecutionStore('safeagent.db')
store.init_db()
result = store.insert_if_not_exists('<requestId>', '<actionName>')
print(json.dumps(result))
```

The JSON printed to stdout is parsed and merged into the n8n item.

---

## Security Considerations

- Request ID and Action Name values are single-quote–escaped before being embedded in the Python
  string literal to prevent injection.
- Postgres credentials are never written to disk; they are passed via an in-memory DSN string
  constructed at runtime.
- The subprocess timeout is 15 seconds. Long-running `init_db()` migrations (first run on a large
  Postgres cluster) may need the timeout raised in the source.

---

## Development

```bash
git clone https://github.com/your-org/n8n-nodes-safeagent
cd n8n-nodes-safeagent
npm install
npm run build      # compiles TypeScript → dist/
npm run dev        # watch mode
npm run lint
```

To test locally against a running n8n instance:

```bash
export N8N_CUSTOM_EXTENSIONS="/path/to/n8n-nodes-safeagent"
n8n start
```

---

## License

MIT © Your Name
