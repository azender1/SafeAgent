"""Reference EC-009 contract. FlowSignal's pinned harness is the authority.

This integration is deliberately limited to the frozen Stripe Test Mode action.
It is not an implementation of production FlowSignal IAM or secret isolation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

FLOW_COMMIT = '7fe99e13456a492a644a8760f126eee6503fc868'
ACTION_HASH = 'bb7dbb55025471a21a216f5e08eea74f72a5a6e1bd0513e127d2f84abcc8de93'
ACCOUNT = 'acct_1U06JYL6P3JlguFB'
TARGET = f'stripe:test:{ACCOUNT}:payment_intent.create'
ACTION = 'payment.collect'


def canonical_bytes(action):
    return json.dumps(action, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def frozen_action(flow_root: Path):
    path = flow_root / 'evidence/EC-009/EC-009_CANONICAL_ACTION.json'
    action = json.loads(path.read_text(encoding='utf-8'))
    digest = hashlib.sha256(canonical_bytes(action)).hexdigest()
    pinned = (flow_root / 'evidence/EC-009/EC-009_CANONICAL_ACTION.sha256').read_text().split()[0]
    if digest != ACTION_HASH or pinned != ACTION_HASH:
        raise ValueError('frozen_action_mismatch')
    if action['beneficiary'] != ACCOUNT or action['target'] != TARGET:
        raise ValueError('literal_account_mismatch')
    return action


def verify_authority(flow_root, req, response, receipt, permit, safe_request, *, now=None):
    """Re-evaluate the authority immediately before provider dispatch."""
    from app.engines.action_binding import action_binding_hash, canonical_action_object
    from app.engines.authority_store import get_authority_state_version
    from app.engines.ec009_projection import stripe_payment_intent_projection
    from app.engines.permit_authority import verify_execution_permit
    from app.engines.receipt_integrity import verify_receipt_hmac
    from safeagent_exec_guard.boundary import PermitDenied

    def require(condition, reason):
        if not condition:
            raise PermitDenied(reason)

    frozen = frozen_action(flow_root)
    actual = canonical_action_object(req)
    require(actual == frozen, 'canonical_action_mismatch')
    require(action_binding_hash(req) == ACTION_HASH, 'canonical_hash_mismatch')
    require(receipt is not None and verify_receipt_hmac(receipt), 'flowsignal_receipt_invalid')
    require(response.decision == receipt.decision == 'ALLOW', 'flowsignal_not_allowed')
    require(response.authority_receipt_id == receipt.id == permit.authority_receipt_id, 'receipt_id_mismatch')
    require(response.valid_until == receipt.valid_until, 'receipt_expiry_mismatch')
    require(receipt.action_binding_hash == permit.action_binding_hash == ACTION_HASH, 'authority_binding_mismatch')
    require(receipt.authority_state_version == permit.authority_state_version == get_authority_state_version(), 'authority_state_stale')
    require(verify_execution_permit(permit), 'flowsignal_permit_invalid')
    require(permit.valid_until == receipt.valid_until.isoformat(), 'permit_expiry_mismatch')
    at = now or datetime.now(timezone.utc)
    require(at.tzinfo is not None and at.astimezone(timezone.utc) <= receipt.valid_until, 'flowsignal_expired')
    require(req.principal_id == safe_request.principal and safe_request.target == TARGET, 'safeagent_identity_mismatch')
    require(safe_request.action == 'stripe.payment_intent.create', 'safeagent_action_mismatch')
    require(safe_request.run_id == 'flowsignal-ec009-' + receipt.id, 'safeagent_run_mismatch')
    expected = stripe_payment_intent_projection(req)
    expected['metadata'].update({
        'flowsignal_authority_receipt_id': receipt.id,
        'flowsignal_authority_state_version': str(receipt.authority_state_version),
    })
    require(safe_request.payload == expected, 'stripe_projection_mismatch')
    return {'canonical_hash': ACTION_HASH, 'upstream_valid_until': receipt.valid_until.isoformat(),
            'verified_at': at.astimezone(timezone.utc).isoformat(), 'authority_state_version': receipt.authority_state_version}


class BoundEC009Gateway:
    """The fixture's only dispatch entry point; upstream check is mandatory."""

    def __init__(self, stripe_gateway, flow_root, req, response, receipt, permit, provider_account):
        self.stripe_gateway = stripe_gateway
        self.flow_root = flow_root
        self.req = req
        self.response = response
        self.receipt = receipt
        self.permit = permit
        self.provider_account = provider_account
        self.checks = []

    def dispatch(self, token, request):
        from safeagent_exec_guard.boundary import PermitDenied

        def check():
            proof = verify_authority(self.flow_root, self.req, self.response,
                                     self.receipt, self.permit, request)
            if self.provider_account != ACCOUNT:
                raise PermitDenied('provider_account_mismatch')
            self.checks.append(proof)

        return self.stripe_gateway.dispatch(token, request, pre_dispatch=check)
