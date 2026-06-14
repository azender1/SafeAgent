import requests
import time
import json
import hashlib

ts = int(time.time())

action_type = "compliance_check"
agent_id = "safeagent-prod"
scope = "mycelium.soma"

preimage = {
    "action_type": action_type,
    "agent_id": agent_id,
    "scope": scope,
    "timestamp": str(ts)
}

canonical = json.dumps(
    dict(sorted(preimage.items())),
    separators=(",", ":"),
    ensure_ascii=False,
).encode()

action_ref = hashlib.sha256(canonical).hexdigest()

payload = {
    "action_ref": action_ref,
    "service": "mycelium.soma",
    "preimage": {
        "agent_id": agent_id,
        "action_type": action_type,
        "scope": scope,
        "ts": ts
    }
}

response = requests.post(
    "https://argentum-api.rgiskard.xyz/nexus/trail",
    json=payload,
    headers={
        "Authorization": "Bearer d768c2022187486ca8aeffc24ce1e3e5",
        "Content-Type": "application/json"
    }
)

print(response.status_code)
print(response.json())