import os

print("\n==============================")
print("WITHOUT EXECUTION GUARD")
print("==============================")

executions = 0

def send_payment():
    global executions
    executions += 1
    print(f"REAL SIDE EFFECT: sending $50 (execution #{executions})")
    return {"status": "sent"}

# first attempt
result1 = send_payment()
print("first attempt result:", result1)

# simulate retry after timeout / uncertainty
result2 = send_payment()
print("retry result:", result2)

print(f"\nPayments executed: {executions} (expected: 1)")
print("❌ Duplicate execution occurred")


print("\n==============================")
print("WITH SAFEAGENT EXECUTION GUARD")
print("==============================")

from safeagent_exec_guard.postgres_store import PostgresExecutionStore

dsn = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
store = PostgresExecutionStore(dsn)
store.init_db()

executions = 0

def guarded_payment():
    global executions
    executions += 1
    print(f"REAL SIDE EFFECT: sending $50 (execution #{executions})")
    return {"status": "sent", "receipt_id": "rcpt_12345"}

request_id = "payment_demo_with_guard"
action = "send_payment"

# first attempt
if store.insert_if_not_exists(request_id, action):
    result1 = guarded_payment()
    store.complete(request_id, result1)
    print("first attempt result:", result1)
else:
    print("first attempt was already recorded")

# retry
if store.insert_if_not_exists(request_id, action):
    result2 = guarded_payment()
    store.complete(request_id, result2)
    print("retry unexpectedly executed:", result2)
else:
    print(">>> duplicate detected, execution blocked")
    print("retry did NOT run side effect again")

print(f"\nPayments executed: {executions} (expected: 1)")
print("✅ Duplicate prevented")