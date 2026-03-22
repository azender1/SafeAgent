import os
import time
from safeagent_exec_guard.postgres_store import PostgresExecutionStore

dsn = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
store = PostgresExecutionStore(dsn)
store.init_db()

request_id = "demo_retry_006"
action = "send_payment"

def fake_side_effect():
    print(">>> SENDING PAYMENT TO WALLET")
    time.sleep(2)
    return {"status": "sent", "receipt_id": "rcpt_12345"}

print("=" * 60)
print("FIRST ATTEMPT")
print(f"request_id={request_id}")
print("=" * 60)

if store.insert_if_not_exists(request_id, action):
    print("Executing action...")
    print(">>> acquiring execution lock in Postgres")
    result = fake_side_effect()
    store.complete(request_id, result)
    print("DONE:", result)
else:
    print("Already executed.")

time.sleep(3)

print()
print("=" * 60)
print("RETRY AFTER TIMEOUT / NETWORK FAILURE")
print(f"request_id={request_id}")
print("=" * 60)

if store.insert_if_not_exists(request_id, action):
    print("Executing action...")
    result = fake_side_effect()
    store.complete(request_id, result)
    print("DONE:", result)
else:
    print(">>> duplicate detected, returning cached result")
    print("Already executed.")