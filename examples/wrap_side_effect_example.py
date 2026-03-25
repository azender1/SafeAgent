from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

store = SQLiteExecutionStore("safeagent.db")
store.init_db()

def safe_execute(request_id: str, action: str, execute_fn):
    if store.insert_if_not_exists(request_id, action):
        result = execute_fn()
        store.complete(request_id, result)
        return result
    else:
        return {"status": "duplicate_blocked"}

def send_email():
    print("sending email...")
    return {"status": "sent", "message_id": "msg_123"}

result1 = safe_execute("email_001", "send_email", send_email)
print("first:", result1)

result2 = safe_execute("email_001", "send_email", send_email)
print("retry:", result2)