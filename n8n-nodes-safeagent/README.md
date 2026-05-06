# n8n-nodes-safeagent

Community node for [SafeAgent](https://github.com/azender1/SafeAgent) — exactly-once execution guard for n8n workflows.

Prevents duplicate side effects (emails, payments, API calls) when webhooks fire twice, agents retry, or workflows restart mid-run.

---

## What it does

n8n workflows can trigger the same action more than once — on retry, timeout, webhook replay, or agent loop. SafeAgent intercepts before the side effect happens and blocks the duplicate.

**Claim-before-execute pattern:**

```
Webhook fires → SafeAgent claims the request_id → action runs → marked SETTLED
Webhook fires again → SafeAgent sees SETTLED → action is skipped
```

One payment. One email. One trade. No matter how many times the workflow runs.

---

## Installation

In your n8n instance, go to **Settings → Community Nodes → Install** and enter:

```
n8n-nodes-safeagent
```

> **Requires:** Python 3.10+ and `safeagent-exec-guard` installed on the host running n8n.
>
> ```bash
> pip install safeagent-exec-guard
> ```
>
> Docker users: extend the base n8n image with Python 3.10 and the pip package. See [Docker setup](#docker-setup) below.

---

## Usage

Add the **SafeAgent** node before any irreversible action in your workflow.

### Fields

| Field | Description |
|---|---|
| `request_id` | Unique ID for this execution (use `{{ $json.headers["x-request-id"] }}` or similar) |
| `action` | Short label for the action being guarded (e.g. `send_email`, `charge_card`) |
| `db_path` | Path to SQLite file for state storage (default: `safeagent.db`) |

### Outputs

- **Proceed** — claim succeeded, run your action
- **Skip** — duplicate detected, bypass the action

### Example: Webhook → Email (duplicate-safe)

```
[Webhook] → [SafeAgent] → (Proceed) → [Send Email]
                        → (Skip)    → [No-op / Log]
```

If the webhook fires twice with the same `request_id`, the email sends once. The second run exits through **Skip**.

---

## State lifecycle

SafeAgent tracks each `request_id` through these states:

```
OPEN → RESOLVED → IN_RECONCILIATION → FINAL → SETTLED
```

Execution is only permitted from `FINAL`. If the agent's signals are ambiguous, the state stays in `IN_RECONCILIATION` and the side effect is blocked until the outcome is clear.

---

## Docker setup

Extend the official n8n image:

```dockerfile
FROM docker.n8n.io/n8nio/n8n

USER root
RUN apk add --no-cache python3 py3-pip && \
    pip3 install safeagent-exec-guard --break-system-packages
USER node
```

Build and run:

```bash
docker build -t n8n-safeagent .
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n n8n-safeagent
```

---

## Distributed / Postgres

For multi-instance n8n setups, use Postgres instead of SQLite:

```bash
pip install safeagent-exec-guard[postgres]
```

Set `db_path` to your Postgres connection string:
```
postgresql://user:password@host:5432/safeagent
```

---

## Links

- **PyPI:** [safeagent-exec-guard](https://pypi.org/project/safeagent-exec-guard/)
- **GitHub:** [azender1/SafeAgent](https://github.com/azender1/SafeAgent)
- **MCP registry:** `io.github.azender1/safeagent`
- **Issues / feedback:** [GitHub Issues](https://github.com/azender1/SafeAgent/issues)

---

## License

MIT © Anthony Zender — [azender1@yahoo.com](mailto:azender1@yahoo.com)

Built in Dayton, OH. USPTO provisional 63/914,036 — Zender Gaming Technologies LLC.
