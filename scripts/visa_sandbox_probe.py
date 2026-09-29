"""One-shot non-consequential Cybersource Intelligent Commerce sandbox probe.

Targets a synthetic/nonexistent instruction ID so it cannot confirm a real
purchase. It proves JWT-v2 authentication and MLE reach the sandbox boundary.
No secret values or decrypted provider payloads are printed.
"""
from __future__ import annotations
import os, uuid
from safeagent_exec_guard.cybersource_transport import CybersourceSandboxTransport

def main() -> int:
    required = [
        "VISA_SANDBOX_ORG_ID",
        "VISA_SANDBOX_ISSUER_MERCHANT_ID",
        "VISA_SANDBOX_TRANSACTING_ID",
        "VISA_SANDBOX_KEY_ID",
        "VISA_SANDBOX_SHARED_SECRET",
        "VISA_SANDBOX_RESPONSE_MLE_P12_B64",
        "VISA_SANDBOX_RESPONSE_MLE_PASSWORD",
        "VISA_SANDBOX_RESPONSE_MLE_KEY_ID",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        print("Visa sandbox probe: NOT READY")
        print("Missing secret names: " + ", ".join(missing))
        return 2

    candidates = [
        ("account_id", os.environ["VISA_SANDBOX_ISSUER_MERCHANT_ID"]),
        ("transacting_id", os.environ["VISA_SANDBOX_TRANSACTING_ID"]),
        ("organization_id", os.environ["VISA_SANDBOX_ORG_ID"]),
    ]

    payload = {
        "clientCorrelationId": str(uuid.uuid4()),
        "transactionData": [{
            "clientReferenceInformation": {"code": "SAFEAGENT-READINESS"},
            "merchantInformation": {
                "merchantDescriptor": {
                    "country": "US",
                    "url": "https://github.com/azender1/SafeAgent",
                },
                "merchantName": "SafeAgent Sandbox Probe",
            },
            "orderInformation": {"amountDetail": {"currency": "USD"}},
        }],
    }

    for label, issuer in candidates:
        transport = CybersourceSandboxTransport(
            issuer_merchant_id=issuer,
            transacting_merchant_id=os.environ["VISA_SANDBOX_TRANSACTING_ID"],
            key_id=os.environ["VISA_SANDBOX_KEY_ID"],
            shared_secret_b64=os.environ["VISA_SANDBOX_SHARED_SECRET"],
            response_mle_kid=os.environ["VISA_SANDBOX_RESPONSE_MLE_KEY_ID"],
            response_mle_p12_b64=os.environ["VISA_SANDBOX_RESPONSE_MLE_P12_B64"],
            response_mle_password=os.environ["VISA_SANDBOX_RESPONSE_MLE_PASSWORD"],
        )
        instruction_id = "safeagent-readiness-" + uuid.uuid4().hex[:12]
        status, response = transport.post(
            f"/acp/v1/instructions/{instruction_id}/confirmations", payload
        )
        if status not in (401, 403):
            print("Visa sandbox probe: REACHED CYBERSOURCE APPLICATION")
            print("Issuer candidate accepted: " + label)
            print(f"HTTP status: {status}")
            print("Target instruction: synthetic/nonexistent")
            print("External consequence created: no")
            return 0

        safe = {}
        if isinstance(response, dict):
            inner = response.get("response")
            if isinstance(inner, dict):
                for key in ("rmsg", "reason", "message", "status"):
                    value = inner.get(key)
                    if isinstance(value, (str, int, float, bool)):
                        safe[key] = value
            for key in ("reason", "message", "status", "rmsg"):
                value = response.get(key)
                if isinstance(value, (str, int, float, bool)):
                    safe.setdefault(key, value)
        print(f"Issuer candidate {label}: HTTP {status}")
        if safe:
            print("Provider diagnostic: " + repr(safe))

    print("Visa sandbox probe: all known issuer candidates rejected")
    return 3

if __name__ == "__main__":
    raise SystemExit(main())
