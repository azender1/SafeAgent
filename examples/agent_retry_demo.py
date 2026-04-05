import random
import time

from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()


# Real irreversible action
def charge_customer(payload):
    print(f"💳 CHARGING CUSTOMER {payload['customer_id']} ${payload['amount']}")
    return {"status": "charged", "amount": payload["amount"]}


def agent_attempt(request_id, payload):
    """Simulate an AI agent retrying a tool call."""

    result = registry.execute(
        request_id=request_id,
        action="charge_customer",
        payload=payload,
        execute_fn=charge_customer,
    )

    print("RESULT:", result)
    print()
    return result


if __name__ == "__main__":

    payload = {
        "customer_id": "CUST123",
        "amount": 49.99,
    }

    request_id = "payment:CUST123:invoice_9842"

    print("----- AGENT CALL 1 -----")
    agent_attempt(request_id, payload)

    time.sleep(1)

    print("----- AGENT RETRY (NETWORK FAILURE) -----")
    agent_attempt(request_id, payload)

    time.sleep(1)

    print("----- AGENT RETRY AGAIN -----")
    agent_attempt(request_id, payload)