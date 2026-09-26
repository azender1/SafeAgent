"""Bearer capabilities for hosted claim settlement and private audit access."""
from __future__ import annotations

import hashlib
import hmac
import json
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


def require_tenant(request: Request) -> str:
    """Resolve a paid caller to a stable tenant, independent of x402 payer."""
    try:
        keys = json.loads(os.getenv('SAFEAGENT_TENANT_KEYS', ''))
        if not isinstance(keys, dict) or not keys or len(set(keys.values())) != len(keys) or any(
            not isinstance(tenant, str) or not tenant or
            not isinstance(key, str) or len(key) < 32
            for tenant, key in keys.items()
        ):
            raise ValueError('invalid tenant key map')
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail='SAFEAGENT_TENANT_KEYS must be configured')
    supplied = request.headers.get('x-safeagent-api-key', '')
    for tenant, key in keys.items():
        if supplied and hmac.compare_digest(supplied, key):
            return tenant
    raise HTTPException(status_code=403, detail='tenant API key required')


def tenant_prefix(tenant: str) -> str:
    return 'tenant:' + hashlib.sha256(tenant.encode()).hexdigest() + ':'


def tenant_request_id(tenant: str, request_id: str) -> str:
    return tenant_prefix(tenant) + request_id


def require_claim_read(request: Request, stored_id: str) -> None:
    """Keep governance envelopes private unless the claim owner authorizes access."""
    if stored_id.startswith('tenant:'):
        tenant = require_tenant(request)
        if not stored_id.startswith(tenant_prefix(tenant)):
            raise HTTPException(status_code=403, detail='tenant access required')
    elif stored_id.startswith('test:'):
        require_settlement_token(request, stored_id)
    else:
        require_audit_token(request)  # historical unscoped records
