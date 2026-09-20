"""Fail-closed capability permits for consequential agent actions.

This module is deliberately outside the agent runtime.  An operator-owned
authority issues a signed permit for one exact effect.  The boundary verifies
the signature and binding, then atomically consumes the permit *before* an
external effect is dispatched.  A consumed permit is never made reusable by
elapsed time or by an ambiguous provider response.

The SQLite store is the single-host reference implementation.  Distributed
deployments must provide the same atomic consume semantics in a shared store.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class BoundaryError(Exception):
    """Base class for boundary failures."""


class PermitDenied(BoundaryError):
    """Raised when a permit cannot authorize the proposed action."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64u(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_bytes(value: Any) -> bytes:
    """RFC 8785 canonical JSON used for every signed or hashed value."""
    return rfc8785.dumps(value)


def payload_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class ActionRequest:
    principal: str
    run_id: str
    action: str
    target: str
    payload: Any

    def __post_init__(self) -> None:
        for field in ("principal", "run_id", "action", "target"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")

    @property
    def payload_sha256(self) -> str:
        return payload_digest(self.payload)


@dataclass(frozen=True)
class PermitClaims:
    permit_id: str
    issuer: str
    principal: str
    run_id: str
    action: str
    target: str
    payload_sha256: str
    issued_at: int
    expires_at: int
    allowed_uses: int = 1
    version: str = "safeagent-permit-v1"


@dataclass(frozen=True)
class DispatchReceipt:
    permit_id: str
    decision: str
    reason: str
    action: str
    target: str
    run_id: str
    dispatched_at: int | None = None
    completed_at: int | None = None
    result: Any = None


class PermitAuthority:
    """Issues Ed25519 permits. Keep this object outside the agent sandbox."""

    def __init__(self, private_key: Ed25519PrivateKey, issuer: str = "safeagent-boundary"):
        self._private_key = private_key
        self.issuer = issuer

    @classmethod
    def generate(cls, issuer: str = "safeagent-boundary") -> "PermitAuthority":
        return cls(Ed25519PrivateKey.generate(), issuer=issuer)

    @classmethod
    def from_private_key_hex(cls, value: str, issuer: str = "safeagent-boundary") -> "PermitAuthority":
        return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(value)), issuer=issuer)

    def private_key_hex(self) -> str:
        return self._private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ).hex()

    def public_key_hex(self) -> str:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ).hex()

    def issue(
        self,
        request: ActionRequest,
        *,
        ttl_seconds: int = 300,
        allowed_uses: int = 1,
        now: int | None = None,
        permit_id: str | None = None,
    ) -> str:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if allowed_uses != 1:
            raise ValueError("safeagent-permit-v1 permits are one-use")
        now = int(time.time()) if now is None else int(now)
        claims = PermitClaims(
            permit_id=permit_id or str(uuid.uuid4()),
            issuer=self.issuer,
            principal=request.principal,
            run_id=request.run_id,
            action=request.action,
            target=request.target,
            payload_sha256=request.payload_sha256,
            issued_at=now,
            expires_at=now + ttl_seconds,
            allowed_uses=allowed_uses,
        )
        encoded = _b64u(canonical_bytes(asdict(claims)))
        signature = _b64u(self._private_key.sign(encoded.encode("ascii")))
        return f"{encoded}.{signature}"


def verify_permit(token: str, public_key_hex: str) -> PermitClaims:
    if not isinstance(token, str) or len(token) > 16_384 or token.count(".") != 1:
        raise PermitDenied("invalid_permit_signature_or_encoding")
    try:
        encoded, signature = token.split(".", 1)
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(_unb64u(signature), encoded.encode("ascii"))
        raw = json.loads(_unb64u(encoded))
        claims = PermitClaims(**raw)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        InvalidSignature,
    ) as exc:
        raise PermitDenied("invalid_permit_signature_or_encoding") from exc
    if claims.version != "safeagent-permit-v1":
        raise PermitDenied("unsupported_permit_version")
    return claims


class SQLitePermitStore:
    """Durable, atomic, single-host permit ledger and outcome journal."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(Path(path))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS boundary_permits (
                    permit_id TEXT PRIMARY KEY,
                    token_sha256 TEXT NOT NULL,
                    claims_json TEXT NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    dispatched_at INTEGER,
                    completed_at INTEGER,
                    result_json TEXT,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS boundary_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    permit_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    detail_json TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def consume(self, token: str, claims: PermitClaims, *, now: int) -> None:
        """Atomically register and consume one use of a signed permit."""
        claims_json = canonical_bytes(asdict(claims)).decode("utf-8")
        token_hash = self._token_hash(token)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM boundary_permits WHERE permit_id = ?", (claims.permit_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO boundary_permits
                    (permit_id, token_sha256, claims_json, uses, status)
                    VALUES (?, ?, ?, 0, 'ISSUED')""",
                    (claims.permit_id, token_hash, claims_json),
                )
                uses = 0
                status = "ISSUED"
            else:
                if row["token_sha256"] != token_hash or row["claims_json"] != claims_json:
                    raise PermitDenied("permit_id_collision")
                uses = int(row["uses"])
                status = str(row["status"])
            if status != "ISSUED" or uses >= claims.allowed_uses:
                raise PermitDenied("permit_already_consumed")
            conn.execute(
                """UPDATE boundary_permits
                SET uses = uses + 1, status = 'DISPATCHED', dispatched_at = ?
                WHERE permit_id = ?""",
                (now, claims.permit_id),
            )
            conn.execute(
                "INSERT INTO boundary_events (permit_id,event_type,occurred_at,detail_json) VALUES (?,?,?,?)",
                (claims.permit_id, "PERMIT_CONSUMED", now, "{}"),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def finalize(self, permit_id: str, *, status: str, reason: str, result: Any, now: int) -> None:
        if status not in {"SETTLED", "REJECTED", "PENDING_RECONCILIATION"}:
            raise ValueError("invalid terminal status")
        result_json = canonical_bytes(result).decode("utf-8") if result is not None else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM boundary_permits WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise BoundaryError("unknown_permit")
            if row["status"] != "DISPATCHED":
                conn.execute("ROLLBACK")
                raise BoundaryError("permit_not_dispatched")
            conn.execute(
                """UPDATE boundary_permits
                SET status=?, completed_at=?, result_json=?, reason=? WHERE permit_id=?""",
                (status, now, result_json, reason, permit_id),
            )
            conn.execute(
                "INSERT INTO boundary_events (permit_id,event_type,occurred_at,detail_json) VALUES (?,?,?,?)",
                (permit_id, status, now, result_json or "{}"),
            )
            conn.execute("COMMIT")

    def get(self, permit_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM boundary_permits WHERE permit_id = ?", (permit_id,)
            ).fetchone()
        return dict(row) if row else None

    def events(self, permit_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM boundary_events WHERE permit_id = ? ORDER BY event_id", (permit_id,)
            ).fetchall()
        return [dict(row) for row in rows]


class BoundaryGateway:
    """Verify, bind and atomically consume permits before dispatch."""

    def __init__(self, public_key_hex: str, store: SQLitePermitStore):
        self.public_key_hex = public_key_hex
        self.store = store

    def _authorize(self, token: str, request: ActionRequest, now: int) -> PermitClaims:
        claims = verify_permit(token, self.public_key_hex)
        if now < claims.issued_at:
            raise PermitDenied("permit_not_yet_valid")
        if now >= claims.expires_at:
            raise PermitDenied("permit_expired")
        comparisons = {
            "principal": request.principal,
            "run_id": request.run_id,
            "action": request.action,
            "target": request.target,
            "payload_sha256": request.payload_sha256,
        }
        for field, actual in comparisons.items():
            if getattr(claims, field) != actual:
                raise PermitDenied(f"{field}_mismatch")
        return claims

    def dispatch(
        self,
        token: str,
        request: ActionRequest,
        executor: Callable[[ActionRequest], Any],
        *,
        now: int | None = None,
    ) -> DispatchReceipt:
        now = int(time.time()) if now is None else int(now)
        claims = self._authorize(token, request, now)
        self.store.consume(token, claims, now=now)
        try:
            result = executor(request)
        except Exception:
            # An exception cannot establish that the provider did not act.
            self.store.finalize(
                claims.permit_id,
                status="PENDING_RECONCILIATION",
                reason="executor_outcome_unknown",
                result=None,
                now=now,
            )
            return DispatchReceipt(
                permit_id=claims.permit_id,
                decision="PENDING_RECONCILIATION",
                reason="executor_outcome_unknown",
                action=request.action,
                target=request.target,
                run_id=request.run_id,
                dispatched_at=now,
                completed_at=now,
            )
        try:
            self.store.finalize(
                claims.permit_id,
                status="SETTLED",
                reason="executor_returned",
                result=result,
                now=now,
            )
        except (TypeError, ValueError):
            # The external effect may have succeeded even if its return value
            # cannot be represented as a durable receipt.  Never reopen it.
            self.store.finalize(
                claims.permit_id,
                status="PENDING_RECONCILIATION",
                reason="result_not_serializable",
                result=None,
                now=now,
            )
            return DispatchReceipt(
                permit_id=claims.permit_id,
                decision="PENDING_RECONCILIATION",
                reason="result_not_serializable",
                action=request.action,
                target=request.target,
                run_id=request.run_id,
                dispatched_at=now,
                completed_at=now,
            )
        return DispatchReceipt(
            permit_id=claims.permit_id,
            decision="SETTLED",
            reason="executor_returned",
            action=request.action,
            target=request.target,
            run_id=request.run_id,
            dispatched_at=now,
            completed_at=now,
            result=result,
        )


__all__ = [
    "ActionRequest",
    "BoundaryError",
    "BoundaryGateway",
    "DispatchReceipt",
    "PermitAuthority",
    "PermitClaims",
    "PermitDenied",
    "SQLitePermitStore",
    "canonical_bytes",
    "payload_digest",
    "verify_permit",
]
