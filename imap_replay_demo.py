import threading, time, random, sqlite3

# ----- Setup (durable store) -----
conn = sqlite3.connect("exec.db", check_same_thread=False)
conn.execute("""
CREATE TABLE IF NOT EXISTS executions (
  event_id TEXT PRIMARY KEY,
  status TEXT,
  result TEXT,
  updated_at REAL
)
""")
conn.commit()
lock = threading.Lock()

# ----- "Side effect" we must not run twice -----
def send_notification(uid):
    print(f"[SIDE EFFECT] send notification for uid={uid}")

# ----- Broken model: no global claim -----
def worker_broken(uid):
    # simulate jitter / races
    time.sleep(random.uniform(0, 0.02))
    # naive "local" dedupe (ineffective across threads)
    send_notification(uid)

def run_broken(uid, workers=5):
    print("\n=== BROKEN (duplicates expected) ===")
    threads = [threading.Thread(target=worker_broken, args=(uid,)) for _ in range(workers)]
    for t in threads: t.start()
    for t in threads: t.join()

# ----- Fixed model: atomic claim + terminal state -----
def claim_event(event_id):
    with lock:
        cur = conn.cursor()
        cur.execute("SELECT status FROM executions WHERE event_id=?", (event_id,))
        row = cur.fetchone()
        if row:
            return False  # already claimed
        cur.execute(
            "INSERT INTO executions(event_id, status, result, updated_at) VALUES(?,?,?,?)",
            (event_id, "in_progress", None, time.time())
        )
        conn.commit()
        return True

def complete_event(event_id, result):
    with lock:
        conn.execute(
            "UPDATE executions SET status=?, result=?, updated_at=? WHERE event_id=?",
            ("completed", result, time.time(), event_id)
        )
        conn.commit()

def worker_fixed(uid):
    event_id = f"imap:inbox:uidvalidity123:{uid}"
    time.sleep(random.uniform(0, 0.02))
    if claim_event(event_id):
        # only one thread gets here
        send_notification(uid)
        complete_event(event_id, "ok")
    else:
        print(f"[SKIP] already claimed uid={uid}")

def run_fixed(uid, workers=5):
    print("\n=== FIXED (exactly-once) ===")
    threads = [threading.Thread(target=worker_fixed, args=(uid,)) for _ in range(workers)]
    for t in threads: t.start()
    for t in threads: t.join()

if __name__ == "__main__":
    uid = 18842
    run_broken(uid)
    run_fixed(uid)