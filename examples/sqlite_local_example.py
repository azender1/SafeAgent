from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

store = SQLiteExecutionStore("safeagent.db")
store.init_db()

request_id = "demo_payment_001"
action = "send_payment"

def do_side_effect():
    print("executing payment...")
    return {"status": "sent", "receipt_id": "rcpt_12345"}

if store.insert_if_not_exists(request_id, action):
    result = do_side_effect()
    store.complete(request_id, result)
    print("executed:", result)
else:
    print("duplicate detected — execution blocked")