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
    print(f"REAL SIDE EFFECT: sending ${amount} to {recipient}")
    payment = {"recipient": recipient, "amount": amount}
    payment_log.append(payment)
    return {"status": "sent", **payment}


if __name__ == "__main__":
    print("=" * 72)
    print("SAFEAGENT MCP RETRY DEMO")
    print("=" * 72)

    print("\nFIRST CALL")
    receipt_1 = send_payment(amount=4200.00, recipient="vendor_abc")
    print(receipt_1)

    print("\nSECOND CALL (SIMULATED RETRY WITH SAME LOGICAL ACTION)")
    receipt_2 = send_payment(amount=4200.00, recipient="vendor_abc")
    print(receipt_2)

    print("\nPAYMENT LOG")
    print(payment_log)

    print("\nSUMMARY")
    print(f"Same execution_id: {receipt_1['execution_id'] == receipt_2['execution_id']}")
    print(f"Payments actually sent: {len(payment_log)}")
    print("Expected payments actually sent: 1")