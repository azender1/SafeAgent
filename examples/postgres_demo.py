from safeagent_exec_guard.postgres_store import PostgresExecutionStore

dsn = "postgresql://postgres:postgres@localhost:5432/postgres"

store = PostgresExecutionStore(dsn)

request_id = "test-123"
action = "send_payment"

store.init_db()

if store.insert_if_not_exists(request_id, action):
    print("Executing action...")
    result = {"status": "sent"}
    store.complete(request_id, result)
    print("DONE:", result)
else:
    existing = store.get(request_id)
    print("Already executed:", existing["result"])