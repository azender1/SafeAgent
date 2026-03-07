from settlement.settlement_requests import SettlementRequestRegistry

registry = SettlementRequestRegistry()


def send_email(payload: dict) -> dict:
    print(f"REAL SIDE EFFECT: sending email to {payload['to']}")
    return {
        "status": "sent",
        "to": payload["to"],
        "template": payload.get("template", "default"),
    }


def safe_tool_execution(request_id: str, action: str, payload: dict):
    return registry.execute(
        request_id=request_id,
        action=action,
        payload=payload,
        execute_fn=send_email,
    )


if __name__ == "__main__":
    tool_call = {
        "request_id": "email:user123:invoice",
        "action": "send_email",
        "payload": {
            "to": "user123@example.com",
            "template": "invoice_reminder",
        },
    }

    print("FIRST CALL")
    receipt1 = safe_tool_execution(
        tool_call["request_id"],
        tool_call["action"],
        tool_call["payload"],
    )
    print(receipt1)

    print("\nSECOND CALL WITH SAME request_id")
    receipt2 = safe_tool_execution(
        tool_call["request_id"],
        tool_call["action"],
        tool_call["payload"],
    )
    print(receipt2)