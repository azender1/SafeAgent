"""Bearer capabilities for hosted claim settlement and private audit access."""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import HTTPException, Request


def _configured_secret(name: str) -> str:
    value = os.getenv(name, '')
    if len(value) < 32:
        raise HTTPException(status_code=503, detail=f'{name} must be configured (32+ characters)')
    return value


def settlement_token(request_id: str) -> str:
    """Deterministic capability; persist the server secret across restarts."""
    secret = _configured_secret('SAFEAGENT_SETTLEMENT_SECRET')
    return hmac.new(secret.encode(), ('settle:' + request_id).encode(), hashlib.sha256).hexdigest()


def has_settlement_token(request: Request, request_id: str) -> bool:
    expected = settlement_token(request_id)
    supplied = request.headers.get('x-safeagent-settlement-token', '')
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def require_settlement_token(request: Request, request_id: str) -> None:
    if not has_settlement_token(request, request_id):
        raise HTTPException(status_code=403, detail='settlement capability required')


def require_audit_token(request: Request) -> None:
    expected = _configured_secret('SAFEAGENT_AUDIT_TOKEN')
    supplied = request.headers.get('x-safeagent-audit-token', '')
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail='audit access required')
