from safeagent_exec_guard import SettlementRequestRegistry
from safeagent_exec_guard.langchain import SafeAgentTool

registry = SettlementRequestRegistry()


def send_email(payload):
    print("REAL SIDE EFFECT: LangChain-style email to", payload["to"])
    return {"status": "sent", "to": payload["to"]}


tool = SafeAgentTool(
    name="send_email",
    func=send_email,
    registry=registry,
    request_id_fn=lambda payload: f"email:{payload['to']}",
)


if __name__ == "__main__":
    payload = {"to": "langchain_user@example.com"}

    print("FIRST CALL")
    print(tool.run(payload))

    print("\nSECOND CALL WITH SAME PAYLOAD")
    print(tool.run(payload))