"""
Async HTTP client for the SafeAgent payment-gated claim server.

Handles the full x402 flow automatically:
    1. POST /claim  → server returns 402 with payment requirement
    2. Client pays using EVM wallet (USDC on Base)
    3. Client retries with payment proof header
    4. Server verifies → returns PROCEED or SKIP

Usage
-----
    import asyncio
    from safeagent_exec_guard.payment_client import PaymentClient

    async def run():
        async with PaymentClient(
            server_url="http://localhost:8402",
            wallet_key="0xYourPrivateKey",  # or set SAFEAGENT_WALLET_KEY
        ) as client:
            result = await client.claim("req-xyz", "email.send")
            if result["status"] == "PROCEED":
                # execute your action here
                outcome = {"ok": True, "sent_to": "user@example.com"}
                await client.settle("req-xyz", outcome)
            elif result["status"] == "SKIP":
                outcome = result["existing"]   # reuse stored result

    asyncio.run(run())
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from x402.client import x402Client
from x402.http.clients.httpx import x402HttpxClient
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

try:
    from eth_account import Account as EthAccount
except ImportError as exc:
    raise ImportError(
        "eth_account is required for PaymentClient. "
        "Install it with: pip install eth-account"
    ) from exc


class PaymentClient:
    """
    Async client for the SafeAgent payment-gated claim server.

    Automatically handles the x402 402 → pay → retry cycle for every
    call to :meth:`claim`.  :meth:`settle` and :meth:`sweep` are free
    and use a plain httpx client.

    Parameters
    ----------
    server_url:
        Base URL of the SafeAgent claim server (e.g. ``http://localhost:8402``).
    wallet_key:
        Hex-encoded EVM private key for signing USDC payments.
        Falls back to the ``SAFEAGENT_WALLET_KEY`` environment variable.
    network:
        CAIP-2 chain ID the server accepts.  Must match the server's
        ``SAFEAGENT_NETWORK`` setting.  Default: ``eip155:84532`` (Base Sepolia).
    """

    def __init__(
        self,
        *,
        server_url: str,
        wallet_key: Optional[str] = None,
        network: str = "eip155:84532",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self._wallet_key = wallet_key or os.environ["SAFEAGENT_WALLET_KEY"]
        self._network = network
        self._x402: Optional[x402HttpxClient] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PaymentClient":
        account = EthAccount.from_key(self._wallet_key)
        signer = EthAccountSigner(account)
        x402_c = x402Client()
        register_exact_evm_client(x402_c, signer, networks=self._network)
        self._x402 = x402HttpxClient(x402_c)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._x402 is not None:
            await self._x402.aclose()
            self._x402 = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def claim(self, request_id: str, action: str) -> Dict[str, Any]:
        """
        Call ``POST /claim`` on the server.

        Pays any 402 automatically using the configured wallet.

        Returns a dict with a ``"status"`` key:

        * ``"PROCEED"``  — claim granted; caller should execute and settle.
        * ``"SKIP"``     — already committed; reuse ``result["existing"]``.
        * ``"PENDING"``  — in-flight by another caller; retry later.
        """
        if self._x402 is None:
            raise RuntimeError("PaymentClient must be used as an async context manager")
        resp = await self._x402.post(
            f"{self.server_url}/claim",
            json={"request_id": request_id, "action": action},
        )
        resp.raise_for_status()
        return resp.json()

    async def settle(self, request_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call ``POST /settle/{request_id}`` — no payment required.

        Transitions the PENDING row to COMMITTED with the execution result.
        """
        if self._x402 is None:
            raise RuntimeError("PaymentClient must be used as an async context manager")
        resp = await self._x402.post(
            f"{self.server_url}/settle/{request_id}",
            json={"result": result},
        )
        resp.raise_for_status()
        return resp.json()

    async def sweep(self) -> Dict[str, Any]:
        """
        Call ``POST /sweep`` — resets stale PENDING rows to CLAIMABLE.

        No payment required.
        """
        if self._x402 is None:
            raise RuntimeError("PaymentClient must be used as an async context manager")
        resp = await self._x402.post(f"{self.server_url}/sweep")
        resp.raise_for_status()
        return resp.json()
