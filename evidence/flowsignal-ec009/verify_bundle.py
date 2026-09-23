#!/usr/bin/env python3
"""Recompute EC-009 claims from primary records; no Stripe network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from contract import ACTION, ACTION_HASH, ACCOUNT, FLOW_COMMIT, TARGET, canonical_bytes, frozen_action, verify_authority


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--flowsignal-root', type=Path, required=True)
    args = parser.parse_args()
    root = args.bundle.resolve()
    flow = args.flowsignal_root.resolve()
    problems = []

    def check(condition, label):
        if not condition:
            problems.append(label)

    def read(name):
        return json.loads((root / 'records' / (name + '.json')).read_text(encoding='utf-8'))

    try:
        from run_fixture import revision
        check(revision(flow) == FLOW_COMMIT, 'wrong FlowSignal checkout')
        safe_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(safe_root))
        sys.path.insert(0, str(flow / 'harness'))
        from app.engines.action_binding import canonical_action_object
        from app.engines.ec009_projection import stripe_payment_intent_projection
        from app.engines.financial_types import AuthorityReceipt, ExecutionResponse, FinancialAuthorityRequest, FinancialCheck
        from app.engines.permit_authority import ExecutionPermit, verify_execution_permit
        from app.engines.receipt_integrity import verify_receipt_hmac
        from safeagent_exec_guard.boundary import payload_digest
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        check(manifest['schema'] in ('safeagent.flowsignal-ec009-offline.v2',
                                    'safeagent.flowsignal-ec009-stripe-test.v1'), 'wrong schema')
        check(manifest['flowsignal_commit'] == FLOW_COMMIT, 'wrong FlowSignal revision')
        check(revision(safe_root) == manifest['safeagent_commit'], 'wrong SafeAgent checkout')
        actual_files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        check(actual_files == manifest['files_sha256'], 'file manifest differs from bundle')
        check(not any(p.name.endswith(('-wal', '-shm')) for p in root.rglob('*')), 'transient SQLite file')
        frozen = frozen_action(flow)
        canonical = read('canonical_action')
        check(canonical == frozen and hashlib.sha256(canonical_bytes(canonical)).hexdigest() == ACTION_HASH,
              'canonical action mismatch')
        check(canonical['action'] == ACTION and canonical['target'] == TARGET and canonical['beneficiary'] == ACCOUNT,
              'literal action/account mismatch')
        req_data = read('flowsignal_authority_request')
        for key in ('mandate_valid_until', 'screening_captured_at', 'requested_execution_time'):
            req_data[key] = datetime.fromisoformat(req_data[key])
        req = FinancialAuthorityRequest(**req_data)
        check(canonical_action_object(req) == canonical, 'request differs from frozen action')
        response = read('flowsignal_authority_response')
        receipt_data = read('flowsignal_authority_receipt')
        for key in ('sealed_at', 'valid_until'):
            receipt_data[key] = datetime.fromisoformat(receipt_data[key]) if receipt_data[key] else None
        receipt_data['checks'] = [FinancialCheck(**c) for c in receipt_data['checks']]
        receipt = AuthorityReceipt(**receipt_data)
        permit_data = read('flowsignal_execution_permit')
        permit = ExecutionPermit(**permit_data)
        check(verify_receipt_hmac(receipt), 'receipt HMAC invalid')
        check(verify_execution_permit(permit), 'upstream permit signature invalid')
        check(receipt.decision == response['decision'] == 'ALLOW', 'FlowSignal ALLOW missing')
        check(all(c.passed for c in receipt.checks), 'FlowSignal authority check failed')
        check(receipt.id == response['authority_receipt_id'] == permit.authority_receipt_id,
              'receipt/permit identity mismatch')
        check(receipt.action_binding_hash == permit.action_binding_hash == ACTION_HASH, 'authority binding mismatch')
        check(receipt.authority_state_version == permit.authority_state_version, 'authority version mismatch')
        check(receipt.valid_until.isoformat() == permit.valid_until == response['valid_until'], 'expiry mismatch')
        check(receipt.request_snapshot['beneficiary'] == ACCOUNT and
              receipt.request_snapshot['target'] == TARGET, 'receipt snapshot account mismatch')
        action = read('safeagent_action_request')
        expected = stripe_payment_intent_projection(req)
        expected['metadata'].update({'flowsignal_authority_receipt_id': receipt.id,
                                     'flowsignal_authority_state_version': str(receipt.authority_state_version)})
        check(action['payload'] == expected, 'Stripe projection differs from FlowSignal action')
        check(action['action'] == 'stripe.payment_intent.create' and action['target'] == TARGET and
              action['principal'] == req.principal_id and action['run_id'] == 'flowsignal-ec009-' + receipt.id,
              'SafeAgent identity differs from FlowSignal action')
        claims = read('safeagent_permit_claims')
        check(claims['principal'] == action['principal'] and claims['run_id'] == action['run_id'] and
              claims['target'] == TARGET and claims['action'] == action['action'] and
              claims['payload_sha256'] == payload_digest(action['payload']), 'SafeAgent permit binding mismatch')
        check(claims['allowed_uses'] == 1 and claims['expires_at'] <= receipt.valid_until.timestamp() and
              claims['issued_at'] <= claims['expires_at'], 'downstream authority lifetime invalid')
        checks = read('safeagent_verification_checks')
        check(len(checks) >= 2 and all(c['canonical_hash'] == ACTION_HASH and
              c['upstream_valid_until'] == receipt.valid_until.isoformat() and
              datetime.fromisoformat(c['verified_at']) <= receipt.valid_until for c in checks),
              'execution-boundary authority check missing/expired')
        first = read('safeagent_first_dispatch')
        boundary = read('safeagent_boundary_record')
        events = read('safeagent_boundary_events')
        replay = read('safeagent_replay_attempt')
        check(first['decision'] == boundary['status'] == 'SETTLED' and boundary['uses'] == 1 and
              first['permit_id'] == claims['permit_id'] == boundary['permit_id'], 'one-use settlement missing')
        check(json.loads(boundary['claims_json']) == claims, 'boundary DB claims differ')
        check(boundary['dispatched_at'] <= claims['expires_at'] and
              boundary['dispatched_at'] <= receipt.valid_until.timestamp(), 'dispatch after expiry')
        check(len([e for e in events if e['event_type'] == 'PERMIT_CONSUMED']) == 1, 'wrong consumption event count')
        check(replay == {'decision': 'BLOCKED', 'reason': 'permit_already_consumed'}, 'replay not blocked')
        op = read('stripe_operation')
        observation = read('stripe_retrieval')
        account = read('provider_account')
        # Mode is selected by mutually exclusive primary provider records,
        # never by a mutually edited manifest and summary.
        simulated_records = (account == {'account_id': ACCOUNT, 'source': 'SIMULATED'} and
                             (root / 'records/simulated_provider_calls.json').is_file() and
                             not (root / 'records/stripe_provider_retrieval.json').exists())
        live_records = (account == {'account_id': ACCOUNT, 'source': 'STRIPE_TEST_API_READBACK',
                                    'endpoint': 'GET /v1/account'} and
                        (root / 'records/stripe_provider_create.json').is_file() and
                        (root / 'records/stripe_provider_retrieval.json').is_file() and
                        not (root / 'records/simulated_provider_calls.json').exists())
        check(simulated_records or live_records, 'provider records have contradictory/unknown scope')
        mode = 'simulated' if simulated_records else 'stripe-test' if live_records else 'unknown'
        schema = ('safeagent.flowsignal-ec009-offline.v2' if simulated_records else
                  'safeagent.flowsignal-ec009-stripe-test.v1')
        scope = ('simulated gateway-controlled path only' if simulated_records else
                 'Stripe Test Mode account readback and PaymentIntent retrieval recorded')
        check(manifest['mode'] == mode and manifest['schema'] == schema and
              manifest['claim_scope'] == scope,
              'manifest claims contradict primary provider records')
        expected_provider_request = dict(expected)
        expected_provider_request['metadata'] = {**expected['metadata'],
                                                 'safeagent_permit_id': claims['permit_id'],
                                                 'safeagent_run_id': action['run_id']}
        if simulated_records:
            sim = read('simulated_provider_calls')
            provider = sim['objects'][0] if len(sim['objects']) == 1 else {}
            created = provider
            check(sim['label'] == 'SIMULATED_NOT_STRIPE_EVIDENCE' and sim['create_calls'] == 1 and
                  sim['retrieve_calls'] == 1 and len(sim['objects']) == 1, 'provider call count wrong')
            check({k: v for k, v in provider.items() if k not in ('id', 'object', 'status')} == expected_provider_request,
                  'provider consequence differs from authorized projection')
            check(provider.get('status') == 'succeeded' and provider.get('object') == 'payment_intent' and
                  provider.get('id', '').startswith('pi_simulated_'), 'simulated provider object invalid')
        elif live_records:
            created = read('stripe_provider_create')
            provider = read('stripe_provider_retrieval')
            check(created.get('id', '').startswith('pi_') and
                  not created.get('id', '').startswith('pi_simulated_') and
                  created.get('object') == provider.get('object') == 'payment_intent' and
                  created.get('livemode') is provider.get('livemode') is False,
                  'recorded Stripe Test Mode object invalid')
            check(created['id'] == provider['id'] and provider['status'] == 'succeeded',
                  'Stripe retrieval did not confirm the created PaymentIntent')
            check(provider['amount'] == expected_provider_request['amount'] and
                  provider['amount_received'] == expected_provider_request['amount'] and
                  provider['currency'] == expected_provider_request['currency'] and
                  provider['payment_method'] == expected_provider_request['payment_method'] and
                  provider['metadata'] == expected_provider_request['metadata'],
                  'Stripe retrieval differs from authorized projection')
            check(created['amount'] == provider['amount'] and created['currency'] == provider['currency'] and
                  created['metadata'] == provider['metadata'], 'Stripe create/retrieval conflict')
        else:
            provider = {}
            created = {}
        check(op['idempotency_key'] == 'safeagent:' + claims['permit_id'] and
              json.loads(op['request_json']) == expected_provider_request, 'Stripe stored request mismatch')
        check(op['payment_intent_id'] == provider.get('id') == observation['payment_intent_id'] and
              op['reconciliation_state'] == observation['state'] == 'CONFIRMED' and
              observation['source'] == 'retrieve', 'provider retrieval mismatch')
        check(first['result'] == json.loads(boundary['result_json']) == created,
              'executor result differs from provider object')
        summary = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
        expected_summary = {
            'mode': mode, 'action_hash': ACTION_HASH, 'flow_commit': FLOW_COMMIT,
            'safeagent_commit': manifest['safeagent_commit'], 'first': first['decision'],
            'replay': replay, 'provider': observation['state'],
            'limitations': (['No external Stripe execution'] if simulated_records else
                            ['Provider records are locally recorded, not independently signed']) +
            ['No production authority isolation', 'No alternate-route closure', 'No commercial validation'],
        }
        for field in expected_summary.keys() | summary.keys():
            check(summary.get(field) == expected_summary.get(field) and
                  (field in summary) == (field in expected_summary),
                  f'summary.{field} contradicts primary records')
        negatives = read('negative_cases')
        required = {'amount', 'currency', 'beneficiary', 'target', 'source_account', 'action',
                    'principal_id', 'actor_id', 'mandate_id', 'purpose', 'l_vs_1', 'expired',
                    'forged_signature', 'receipt_id', 'state_version', 'refuse', 'payload',
                    'safe_target', 'safe_run'}
        check(set(negatives) == required and all(v != 'FAILED_OPEN' for v in negatives.values()),
              'negative cases missing or failed open')
        from safeagent_exec_guard.boundary import ActionRequest, PermitDenied
        safe_request = ActionRequest(**action)
        response_object = ExecutionResponse(**{**response, 'valid_until': datetime.fromisoformat(response['valid_until'])})
        for label, changed in {
            'amount': {'req': replace(req, amount=1.01)},
            'beneficiary': {'req': replace(req, beneficiary='acct_ATTACKER')},
            'l_vs_1': {'req': replace(req, beneficiary='acct_1U06JYL6P3J1guFB')},
            'forged_signature': {'permit': replace(permit, signature='0' * 64)},
            'expired': {'now': receipt.valid_until + timedelta(microseconds=1)},
            'safe_target': {'safe_request': replace(safe_request, target='stripe:test:acct_ATTACKER:payment_intent.create')},
        }.items():
            values = {'req': req, 'permit': permit, 'safe_request': safe_request,
                      'now': receipt.sealed_at}
            values.update(changed)
            try:
                verify_authority(flow, values['req'], response_object, receipt, values['permit'],
                                 values['safe_request'], now=values['now'])
                check(False, f'negative {label} failed open when independently rerun')
            except PermitDenied as exc:
                check(negatives[label] == exc.reason, f'negative {label} result differs from rerun')
        with sqlite3.connect(root / 'runtime/safeagent-permits.db') as db:
            row = db.execute('SELECT permit_id, uses, status FROM boundary_permits').fetchall()
            check(row == [(claims['permit_id'], 1, 'SETTLED')], 'durable permit DB mismatch')
        with sqlite3.connect(root / 'runtime/safeagent-stripe.db') as db:
            row = db.execute('SELECT permit_id, payment_intent_id, reconciliation_state FROM stripe_operations').fetchall()
            check(row == [(claims['permit_id'], provider.get('id'), 'CONFIRMED')], 'durable Stripe DB mismatch')
    except Exception as exc:
        problems.append(f'verification error: {type(exc).__name__}: {exc}')
    print(json.dumps({'mode': locals().get('mode', 'unknown'), 'passed': not problems,
                      'problems': problems}, indent=2))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
