from safeagent_exec_guard import SettlementRequestRegistry
from langchain.tools import tool

registry = SettlementRequestRegistry()


def send_email(payload: dict):
    print("REAL SIDE EFFECT: LangChain email to", payload["to"])
    return {"status": "sent", "to": payload["to"]}


@tool
def send_email_tool(payload: dict):
    """Send an email through a LangChain-style tool interface."""
    return send_email(payload)


def safe_langchain_tool(request_id, payload):
    return registry.execute(
        request_id=request_id,
        action="send_email",
        payload=payload,
        execute_fn=send_email,
    )


if __name__ == "__main__":
    payload = {"to": "user@example.com"}

    print("FIRST CALL")
    print(safe_langchain_tool("langchain_email_1", payload))

    print("SECOND CALL WITH SAME request_id")
    print(safe_langchain_tool("langchain_email_1", payload))