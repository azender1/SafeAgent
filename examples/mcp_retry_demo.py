"""
MCP Retry Demo for SafeAgent

Shows how a side-effecting MCP-style tool can be wrapped so retries return
a cached receipt instead of executing the side effect again.
"""

from safeagent_exec_guard import SettlementRequestRegistry
from safeagent_exec_guard.mcp import safe_mcp_tool


def mcp_tool(func):
    """Tiny stand-in for a real MCP tool decorator."""
    return func


registry = SettlementRequestRegistry()
payment_log = []


@mcp_tool
@safe_mcp_tool(
    registry=registry,
    action="send_payment",
    request_id_fn=lambda payload: f"payment:{payload['recipient']}:{payload['amount']}",
)
def send_payment(amount: float, recipient: str) -> dict:
    print(f"REAL SIDE EFFECT: sending ${amount:.2f} to {recipient}")
    payment = {"recipient": recipient, "amount": amount}
    payment_log.append(payment)
    return {"status": "SETTLED", **payment}


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("SAFEAGENT MCP RETRY DEMO")
    print("=" * 60)

    print("\n[attempt 1]")
    receipt_1 = send_payment(amount=50.00, recipient="alice@example.com")
    print("receipt:", receipt_1)

    print("\n[attempt 2 - retry]")
    receipt_2 = send_payment(amount=50.00, recipient="alice@example.com")
    print("DUPLICATE BLOCKED — returning cached receipt")
    print("receipt:", receipt_2)

    print("\n[attempt 3 - retry]")
    receipt_3 = send_payment(amount=50.00, recipient="alice@example.com")
    print("DUPLICATE BLOCKED — returning cached receipt")
    print("receipt:", receipt_3)

    print("\nSUMMARY")
    print(f"Same execution_id: {receipt_1['execution_id'] == receipt_2['execution_id']}")
    print(f"Payments actually sent: {len(payment_log)} (expected: 1)")
