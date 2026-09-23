#!/usr/bin/env python3
"""Offline EC-009 operator fixture. No Stripe credentials or network calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract import ACTION_HASH, ACCOUNT, FLOW_COMMIT, TARGET, BoundEC009Gateway, frozen_action, verify_authority

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST_PRIVATE_KEY_HEX = '11' * 32  # offline fixture only, never a production key


def plain(value: Any) -> Any:
    if hasattr(value, 'to_dict_recursive'):
        value = value.to_dict_recursive()
    elif hasattr(value, '__dataclass_fields__'):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def write(root: Path, name: str, value: Any) -> None:
    path = root / 'records' / (name + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plain(value), sort_keys=True, indent=2) + '\n', encoding='utf-8')


class SimulatedStripe:
    account_id = ACCOUNT

    def __init__(self):
        self.create_calls = 0
        self.retrieve_calls = 0
        self.created = {}

    def create(self, **params):
        self.create_calls += 1
        key = params.pop('idempotency_key')
        if key not in self.created:
            self.created[key] = {'id': 'pi_simulated_' + hashlib.sha256(key.encode()).hexdigest()[:24],
                                 'object': 'payment_intent', 'status': 'succeeded', **params}
        return dict(self.created[key])

    def retrieve(self, payment_intent_id):
        self.retrieve_calls += 1
        return next(dict(v) for v in self.created.values() if v['id'] == payment_intent_id)


def revision(root):
    return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()


def snapshot_database(source, target):
    # SQLite backup creates a stable standalone DB after all write transactions.
    with sqlite3.connect(source) as inp, sqlite3.connect(target) as out:
        inp.backup(out)
        out.execute('PRAGMA journal_mode=DELETE')


def manifest(root, safe_commit):
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'manifest.json':
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'schema': 'safeagent.flowsignal-ec009-offline.v2', 'mode': 'simulated',
            'flowsignal_commit': FLOW_COMMIT, 'safeagent_commit': safe_commit,
            'files_sha256': files, 'claim_scope': 'simulated gateway-controlled path only'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--flowsignal-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, default=HERE / 'evidence' / 'runs')
    args = parser.parse_args()
    flow_root = args.flowsignal_root.resolve()
    if revision(flow_root) != FLOW_COMMIT:
        raise SystemExit('FlowSignal checkout must be pinned to ' + FLOW_COMMIT)
    frozen = frozen_action(flow_root)
    safe_commit = revision(REPO)
    sys.path.insert(0, str(flow_root / 'harness'))
    sys.path.insert(0, str(REPO))
    from app.engines.action_binding import canonical_action_object
    from app.engines.ec009_projection import stripe_payment_intent_projection
    from app.engines.execution_gateway import ExecutionAttempt, validate_execution
    from app.engines.financial_runtime import evaluate_financial
    from harness.runner import load_scenario
    from safeagent_exec_guard import (ActionRequest, BoundaryGateway, PermitAuthority,
                                      PermitDenied, SQLitePermitStore, SQLiteStripeStore,
                                      StripePaymentIntentGateway)
    from safeagent_exec_guard.boundary import verify_permit

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:12]
    output = output_root / run_id
    output.mkdir()  # never reuse or overwrite an evidence directory
    with tempfile.TemporaryDirectory(prefix='safeagent-ec009-') as temp:
        runtime = Path(temp)
        os.environ['FLOWSIGNAL_PERMIT_CONSUMPTION_STORE'] = str(runtime / 'flowsignal-permits.db')
        os.environ['FLOWSIGNAL_CONSEQUENCE_OUTCOME_STORE'] = str(runtime / 'flowsignal-outcomes.db')
        os.environ['FLOWSIGNAL_ROLLBACK_ANCHOR_STORE'] = str(runtime / 'flowsignal-anchor.db')
        req = load_scenario(flow_root / 'harness/harness/scenarios/EC-009_stripe_usd_collection.json')
        if canonical_action_object(req) != frozen:
            raise SystemExit('FlowSignal action differs from frozen action')
        response, receipt = evaluate_financial(req)
        if response.decision != 'ALLOW' or receipt is None:
            raise SystemExit('FlowSignal did not ALLOW the frozen action')
        attempt = ExecutionAttempt(**{k: getattr(req, k) for k in (
            'actor_id', 'principal_id', 'action', 'target', 'amount', 'currency',
            'source_account', 'beneficiary', 'purpose', 'mandate_id')},
            attempted_at=datetime.now(timezone.utc))
        result = validate_execution(receipt, attempt)
        permit = result.execution_permit
        if result.status != 'PERMITTED' or permit is None:
            raise SystemExit('FlowSignal did not issue an execution permit')
        payload = stripe_payment_intent_projection(req)
        payload['metadata'].update({
            'flowsignal_authority_receipt_id': receipt.id,
            'flowsignal_authority_state_version': str(receipt.authority_state_version),
        })
        safe_request = ActionRequest(req.principal_id, 'flowsignal-ec009-' + receipt.id,
                                     'stripe.payment_intent.create', TARGET, payload)
        simulated = SimulatedStripe()
        verify_authority(flow_root, req, response, receipt, permit, safe_request)
        expires = int(receipt.valid_until.timestamp())
        now = int(datetime.now(timezone.utc).timestamp())
        if expires <= now:
            raise SystemExit('upstream authority expired before SafeAgent permit issuance')
        authority = PermitAuthority.from_private_key_hex(TEST_PRIVATE_KEY_HEX, issuer='offline-ec009-reference')
        permit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'flowsignal:' + permit.signature))
        token = authority.issue(safe_request, now=now, ttl_seconds=expires - now, permit_id=permit_id)
        claims = verify_permit(token, authority.public_key_hex())
        if claims.expires_at > receipt.valid_until.timestamp():
            raise SystemExit('SafeAgent capability outlives FlowSignal authority')
        permit_store = SQLitePermitStore(runtime / 'safeagent-permits.db')
        stripe_store = SQLiteStripeStore(runtime / 'safeagent-stripe.db')
        stripe_gateway = StripePaymentIntentGateway(BoundaryGateway(authority.public_key_hex(), permit_store),
                                                    simulated, stripe_store)
        gateway = BoundEC009Gateway(stripe_gateway, flow_root, req, response, receipt, permit,
                                   simulated.account_id)
        first = gateway.dispatch(token, safe_request)
        try:
            gateway.dispatch(token, safe_request)
            replay = {'decision': 'ERROR', 'reason': 'replay dispatched'}
        except PermitDenied as exc:
            replay = {'decision': 'BLOCKED', 'reason': exc.reason}
        observation = stripe_gateway.reconcile(first.permit_id)
        negatives = {}
        mutations = {
            'amount': replace(req, amount=1.01), 'currency': replace(req, currency='GBP'),
            'beneficiary': replace(req, beneficiary='acct_ATTACKER'),
            'target': replace(req, target='stripe:test:acct_ATTACKER:payment_intent.create'),
            'source_account': replace(req, source_account='pm_card_mastercard'),
            'action': replace(req, action='payment.release'),
            'principal_id': replace(req, principal_id='attacker'),
            'actor_id': replace(req, actor_id='attacker'),
            'mandate_id': replace(req, mandate_id='attacker'),
            'purpose': replace(req, purpose='changed'),
            'l_vs_1': replace(req, beneficiary='acct_1U06JYL6P3J1guFB'),
        }
        for label, mutant in mutations.items():
            try:
                verify_authority(flow_root, mutant, response, receipt, permit, safe_request)
                negatives[label] = 'FAILED_OPEN'
            except PermitDenied as exc:
                negatives[label] = exc.reason
        for label, changed in {
            'expired': {'now': receipt.valid_until.replace(year=receipt.valid_until.year + 1)},
            'forged_signature': {'permit': replace(permit, signature='0' * 64)},
            'receipt_id': {'permit': replace(permit, authority_receipt_id='attacker')},
            'state_version': {'permit': replace(permit, authority_state_version=-1)},
            'refuse': {'response': replace(response, decision='REFUSE')},
        }.items():
            values = {'now': None, 'permit': permit, 'response': response}
            values.update(changed)
            try:
                verify_authority(flow_root, req, values['response'], receipt, values['permit'],
                                 safe_request, now=values['now'])
                negatives[label] = 'FAILED_OPEN'
            except PermitDenied as exc:
                negatives[label] = exc.reason
        for label, changed_request in {
            'payload': replace(safe_request, payload={**payload, 'amount': 101}),
            'safe_target': replace(safe_request, target='stripe:test:acct_ATTACKER:payment_intent.create'),
            'safe_run': replace(safe_request, run_id='attacker'),
        }.items():
            try:
                verify_authority(flow_root, req, response, receipt, permit, changed_request)
                negatives[label] = 'FAILED_OPEN'
            except PermitDenied as exc:
                negatives[label] = exc.reason

        write(output, 'canonical_action', frozen)
        write(output, 'flowsignal_authority_request', req)
        write(output, 'flowsignal_authority_response', response)
        write(output, 'flowsignal_authority_receipt', receipt)
        write(output, 'flowsignal_gateway_result', result)
        write(output, 'flowsignal_execution_permit', permit)
        write(output, 'safeagent_action_request', safe_request)
        write(output, 'safeagent_permit_claims', claims)
        write(output, 'safeagent_verification_checks', gateway.checks)
        write(output, 'safeagent_first_dispatch', first)
        write(output, 'safeagent_replay_attempt', replay)
        write(output, 'safeagent_boundary_record', permit_store.get(first.permit_id))
        write(output, 'safeagent_boundary_events', permit_store.events(first.permit_id))
        write(output, 'stripe_operation', stripe_store.get(first.permit_id))
        write(output, 'stripe_retrieval', observation)
        write(output, 'provider_account', {'account_id': simulated.account_id, 'source': 'SIMULATED'})
        write(output, 'simulated_provider_calls', {
            'label': 'SIMULATED_NOT_STRIPE_EVIDENCE', 'create_calls': simulated.create_calls,
            'retrieve_calls': simulated.retrieve_calls, 'objects': list(simulated.created.values()),
        })
        write(output, 'negative_cases', negatives)
        stable = output / 'runtime'
        stable.mkdir()
        for name in ('safeagent-permits.db', 'safeagent-stripe.db'):
            snapshot_database(runtime / name, stable / name)
    summary = {'mode': 'simulated', 'action_hash': ACTION_HASH, 'flow_commit': FLOW_COMMIT,
               'safeagent_commit': safe_commit, 'first': first.decision,
               'replay': replay, 'provider': observation.state,
               'limitations': ['No external Stripe execution', 'No production authority isolation',
                               'No alternate-route closure', 'No commercial validation']}
    (output / 'summary.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
    (output / 'manifest.json').write_text(json.dumps(manifest(output, safe_commit), sort_keys=True, indent=2) + '\n')
    print(json.dumps({'bundle': str(output), **summary}, indent=2))
    return 0 if first.decision == 'SETTLED' and replay['decision'] == 'BLOCKED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
