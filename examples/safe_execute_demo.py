import sys
import os

# Force local repo root to the front of sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import settlement.settlement_requests as m

print("MODULE:", m.__file__)
r = m.SettlementRequestRegistry()
print("HAS execute:", hasattr(r, "execute"))

def do_side_effect():
    print("SIDE EFFECT RAN")

print("FIRST:")
print(r.execute(request_id="abc123", action="send_email", payload={"to": "x"}, execute_fn=do_side_effect))

print("\nREPLAY:")
print(r.execute(request_id="abc123", action="send_email", payload={"to": "x"}, execute_fn=do_side_effect))