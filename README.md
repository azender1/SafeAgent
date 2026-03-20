# SafeAgent

[![PyPI version](https://img.shields.io/pypi/v/safeagent-exec-guard.svg?cacheSeconds=300)](https://pypi.org/project/safeagent-exec-guard/)
[![Python versions](https://img.shields.io/pypi/pyversions/safeagent-exec-guard.svg)](https://pypi.org/project/safeagent-exec-guard/)
[![License](https://img.shields.io/pypi/l/safeagent-exec-guard.svg)](https://github.com/azender1/SafeAgent/blob/main/LICENSE)

Execution guard for AI agent side effects that prevents duplicate execution across retries.

## Demo

![SafeAgent Demo](https://raw.githubusercontent.com/azender1/SafeAgent/main/assets/safeagent-demo.gif)

LLM agents retry tool calls.

That can duplicate side effects:

- payments
- emails
- trades
- tickets
- payouts

SafeAgent is designed to prevent duplicate execution of irreversible actions using request IDs and optional durable state, such as Postgres.

## Install

    pip install safeagent-exec-guard

Python 3.10+

## Why this exists

AI systems retry operations constantly:

- agent loops retry tool calls
- HTTP clients retry failed requests
- queue workers replay jobs
- orchestrators restart workflows

Without protection:

    retry -> duplicate payment
    retry -> duplicate email
    retry -> duplicate ticket
    retry -> duplicate payout

SafeAgent inserts an execution guard between the decision and the irreversible action:

    agent decision
    ↓
    request_id generated
    ↓
    SafeAgent execution guard
    ↓
    side effect executes once
    ↓
    future retries return cached receipt

## Minimal Example

    from safeagent_exec_guard import SettlementRequestRegistry

    registry = SettlementRequestRegistry()

    def send_email(payload):
        print("SENDING EMAIL:", payload["to"])

    receipt = registry.execute(
        request_id="email:C123:invoice",
        action="send_email",
        payload={"to": "c123@example.com"},
        execute_fn=send_email,
    )

    print(receipt)

Example output:

    First call:
    SENDING EMAIL: c123@example.com
    {'ok': True, 'reason': 'executed', 'request_id': 'email:C123:invoice'}

    Second call (retry):
    {'ok': True, 'reason': 'dedup_same_request_id', 'request_id': 'email:C123:invoice'}

Running this twice only executes the side effect once. Later calls return the stored receipt.

## Decorator API

    from safeagent_exec_guard import SettlementRequestRegistry, safeagent_guard

    registry = SettlementRequestRegistry()

    @safeagent_guard(
        registry=registry,
        action="send_email",
        request_id_fn=lambda payload: f"email:{payload['to']}:{payload.get('template','default')}",
    )
    def send_email(payload):
        print("REAL SIDE EFFECT:", payload["to"])

    send_email({"to": "user@example.com", "template": "invoice"})
    send_email({"to": "user@example.com", "template": "invoice"})

The second call returns the cached receipt instead of executing again.

## MCP Example

    from safeagent_exec_guard import SettlementRequestRegistry
    from safeagent_exec_guard.mcp import safe_mcp_tool

    registry = SettlementRequestRegistry()

    @safe_mcp_tool(
        registry=registry,
        action="send_payment",
        request_id_fn=lambda payload: f"payment:{payload['recipient']}:{payload['amount']}",
    )
    def send_payment(amount: float, recipient: str):
        print(f"REAL SIDE EFFECT: sending ${amount} to {recipient}")

Run:

    python examples/mcp_retry_demo.py

## Durable execution (Postgres)

SafeAgent can optionally use a Postgres-backed execution store.

This allows execution guarantees to hold across:

- process restarts
- multiple workers
- distributed systems

Without durable state, guarantees are limited to a single process.

Example:

    from safeagent_exec_guard import SettlementRequestRegistry
    from safeagent_exec_guard.postgres_store import PostgresExecutionStore

    store = PostgresExecutionStore(
        dsn="postgresql://postgres:postgres@localhost:5432/postgres"
    )

    registry = SettlementRequestRegistry(postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres")

    def send_payment(payload):
        print("REAL SIDE EFFECT:", payload)

    registry.execute(
        request_id="payment:alice:50",
        action="send_payment",
        payload={"amount": 50, "recipient": "alice"},
        execute_fn=send_payment,
    )

Run twice:

    First run:
    REAL SIDE EFFECT: {'amount': 50, 'recipient': 'alice'}

    Second run:
    Already executed: {'status': 'sent'}

## Framework Examples

    python examples/openai_tool_safeagent.py
    python examples/langchain_safeagent.py
    python examples/crewai_safeagent.py
    python examples/decorator_safeagent.py
    python examples/langchain_adapter_safeagent.py
    python examples/mcp_retry_demo.py
    python examples/postgres_demo.py

## Failure semantics

SafeAgent records an execution receipt for each `request_id`.

### Retry behavior

    same request_id -> return stored receipt

The side effect is not executed again.

### Timeout after execution

    execution completed
    response lost
    caller retries

SafeAgent returns the stored receipt.

### Partial failures

SafeAgent does not attempt automatic rollback.

Applications should handle partial commits using:

- audit logs
- reconciliation processes
- compensating actions

SafeAgent is designed to enforce at-most-once execution of irreversible actions, not business policy validation.

## Run with Docker

Start Postgres:

    docker compose up -d postgres

Run the Postgres demo:

    docker compose run --rm safeagent python examples/postgres_demo.py

This uses a local Postgres container so execution receipts persist outside a single Python process.

## License

Apache-2.0

## Need help with agent reliability?

If you're building AI agents that trigger real-world actions such as payments, emails, or trades, retries can create duplicate execution and real risk.

If you're dealing with this in production or thinking about how to handle it, feel free to reach out.

Happy to:

- review your architecture
- help design execution safety layers
- implement SafeAgent-style guards in your system

This problem gets tricky quickly once retries and distributed systems are involved.
