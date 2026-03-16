from safeagent_exec_guard import SettlementRequestRegistry, safeagent_guard

registry = SettlementRequestRegistry()


@safeagent_guard(
    registry=registry,
    action="send_email",
    request_id_fn=lambda payload: f"email:{payload['to']}:{payload.get('template', 'default')}",
)
def send_email(payload):
    print("REAL SIDE EFFECT: sending email to", payload["to"])
    return {
        "status": "sent",
        "to": payload["to"],
        "template": payload.get("template", "default"),
    }


if __name__ == "__main__":
    payload = {
        "to": "user@example.com",
        "template": "invoice_reminder",
    }

    print("FIRST CALL")
    print(send_email(payload))

    print("\nSECOND CALL WITH SAME PAYLOAD")
    print(send_email(payload))