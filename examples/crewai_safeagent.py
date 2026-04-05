from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def crew_send_email(payload):
    print("CREW AI SENT EMAIL TO", payload["to"])
    return {"status": "sent"}

def crew_safe_action(request_id, payload):

    return registry.execute(
        request_id=request_id,
        action="send_email",
        payload=payload,
        execute_fn=crew_send_email,
    )

if __name__ == "__main__":

    payload = {"to": "crew@example.com"}

    print("FIRST CALL")
    print(crew_safe_action("crew_email_1", payload))

    print("SECOND CALL")
    print(crew_safe_action("crew_email_1", payload))