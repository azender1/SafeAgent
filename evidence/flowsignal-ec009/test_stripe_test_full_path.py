#!/usr/bin/env python3
"""Run the entire gated EC-009 route with fake Stripe and blocked sockets.

The resulting locally consistent bundle is FAKE, never external Stripe proof.
All runs and mock modules are confined to a temporary directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCOUNT = 'acct_1U06JYL6P3JlguFB'
FAKE_STRIPE = '''\
"""Fake Stripe SDK for the offline full-path regression. No network access."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import socket
assert socket.socket.connect.__name__ == 'reject', 'socket blocking shim was not loaded'

def log(event, **fields):
    with Path(os.environ['EC009_FAKE_LOG']).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'event': event, **fields}, sort_keys=True) + '\\n')

class FakePaymentIntents:
    def __init__(self):
        self.obj = None

    def create(self, params, options):
        log('create', params=params, idempotency_key=options['idempotency_key'])
        self.obj = {
            **params, 'id': 'pi_FAKE_OFFLINE_' + options['idempotency_key'][-12:],
            'object': 'payment_intent', 'status': 'succeeded',
            'amount_received': params['amount'], 'livemode': False,
            'client_secret': 'never_record_this_secret',
        }
        if os.environ['EC009_FAKE_SCENARIO'] == 'lost':
            raise TimeoutError('fake response lost after creation')
        if os.environ['EC009_FAKE_SCENARIO'] == 'missing_id':
            return {**self.obj, 'id': None}
        return dict(self.obj)

    def retrieve(self, payment_intent_id):
        log('retrieve', payment_intent_id=payment_intent_id)
        if self.obj is None or payment_intent_id != self.obj['id']:
            raise AssertionError('retrieval must use the ID from this creation')
        return dict(self.obj)

class StripeClient:
    def __init__(self, key, max_network_retries):
        assert key == 'sk_test_FAKE_OFFLINE_ONLY' and max_network_retries == 0
        self.v1 = SimpleNamespace(payment_intents=FakePaymentIntents())

    def raw_request(self, method, path):
        log('account', method=method, path=path)
        assert (method, path) == ('get', '/v1/account')
        return SimpleNamespace(data={
            'id': os.environ['EC009_FAKE_ACCOUNT'], 'object': 'account',
        })
'''
DENY_NETWORK = '''\
import socket
def reject(*args, **kwargs):
    raise AssertionError('network forbidden in EC-009 full-path fake regression')
socket.socket.connect = reject
socket.socket.connect_ex = reject
socket.create_connection = reject
'''


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def events(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def run_case(tmp, flow, scenario, account=ACCOUNT):
    name = scenario + ('-wrong-account' if account != ACCOUNT else '')
    output = tmp / name
    log = tmp / (name + '.jsonl')
    env = dict(os.environ)
    env.update({
        'PYTHONPATH': str(tmp / 'fake_sdk'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'STRIPE_SECRET_KEY': 'sk_test_FAKE_OFFLINE_ONLY',
        'EC009_FAKE_SCENARIO': scenario,
        'EC009_FAKE_ACCOUNT': account,
        'EC009_FAKE_LOG': str(log),
    })
    # A subprocess runs the exact operator command, including all fixtures,
    # SQLite journals, evidence writing, and the CLI's review assertion.
    process = subprocess.run([
        sys.executable, str(HERE / 'run_fixture.py'), '--flowsignal-root', str(flow),
        '--output-root', str(output), '--mode', 'stripe-test', '--review-cleared',
    ], env=env, text=True, capture_output=True)
    bundles = list(output.iterdir()) if output.is_dir() else []
    assert len(bundles) == 1, (scenario, process.stderr, bundles)
    return process, bundles[0], events(log)


def verify(bundle, flow, fake_sdk):
    process = subprocess.run([
        sys.executable, str(HERE / 'verify_bundle.py'), str(bundle),
        '--flowsignal-root', str(flow),
    ], text=True, capture_output=True, env={**os.environ,
                                           'PYTHONPATH': str(fake_sdk),
                                           'PYTHONDONTWRITEBYTECODE': '1'})
    assert process.stdout, process.stderr
    return process.returncode, json.loads(process.stdout)


def check_uncertain(bundle, calls, scenario):
    assert [e['event'] for e in calls] == ['account', 'create'], calls
    uncertain = read(bundle / 'uncertain.json')
    assert uncertain['state'] == 'UNCERTAIN' and uncertain['phase'] == 'unknown_payment_intent_id'
    assert uncertain['boundary_receipt']['decision'] == 'PENDING_RECONCILIATION'
    assert not (bundle / 'manifest.json').exists(), 'uncertain run must not claim verification'
    with sqlite3.connect(bundle / 'runtime/safeagent-stripe.db') as db:
        rows = db.execute('SELECT idempotency_key, payment_intent_id, reconciliation_state, last_error '
                          'FROM stripe_operations').fetchall()
        assert len(rows) == 1 and rows[0][0] == calls[1]['idempotency_key']
        assert rows[0][1:3] == (None, 'UNCERTAIN'), rows
        if scenario == 'lost':
            assert 'fake response lost after creation' in rows[0][3]
        else:
            assert rows[0][3] == 'unknown PaymentIntent ID'
    with sqlite3.connect(bundle / 'runtime/safeagent-permits.db') as db:
        rows = db.execute('SELECT uses, status FROM boundary_permits').fetchall()
        assert rows == [(1, 'PENDING_RECONCILIATION')], rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--flowsignal-root', type=Path, required=True)
    args = parser.parse_args()
    flow = args.flowsignal_root.resolve()
    with tempfile.TemporaryDirectory(prefix='ec009-full-path-fake-') as directory:
        tmp = Path(directory)
        fake = tmp / 'fake_sdk'
        fake.mkdir()
        (fake / 'stripe.py').write_text(FAKE_STRIPE, encoding='utf-8')
        (fake / 'sitecustomize.py').write_text(DENY_NETWORK, encoding='utf-8')

        mismatch, mismatch_bundle, mismatch_calls = run_case(tmp, flow, 'success', 'acct_WRONG')
        assert mismatch.returncode != 0 and [e['event'] for e in mismatch_calls] == ['account']
        assert not (mismatch_bundle / 'manifest.json').exists()

        success, bundle, calls = run_case(tmp, flow, 'success')
        assert success.returncode == 0, success.stderr
        assert [e['event'] for e in calls] == ['account', 'create', 'retrieve'], calls
        records = bundle / 'records'
        claims = read(records / 'safeagent_permit_claims.json')
        op = read(records / 'stripe_operation.json')
        created = read(records / 'stripe_provider_create.json')
        retrieved = read(records / 'stripe_provider_retrieval.json')
        assert calls[1]['idempotency_key'] == op['idempotency_key'] == 'safeagent:' + claims['permit_id']
        assert calls[1]['params'] == json.loads(op['request_json'])
        assert calls[2]['payment_intent_id'] == op['payment_intent_id'] == created['id'] == retrieved['id']
        assert read(records / 'safeagent_replay_attempt.json') == {
            'decision': 'BLOCKED', 'reason': 'permit_already_consumed',
        }
        assert read(records / 'safeagent_boundary_record.json')['status'] == 'SETTLED'
        assert len(read(records / 'safeagent_verification_checks.json')) >= 2
        assert 'client_secret' not in created and 'client_secret' not in retrieved
        assert read(bundle / 'summary.json')['limitations'][0] == (
            'Provider records are locally recorded, not independently signed'
        )
        assert not (records / 'simulated_provider_calls.json').exists()
        with sqlite3.connect(bundle / 'runtime/safeagent-stripe.db') as db:
            assert db.execute('SELECT payment_intent_id, reconciliation_state FROM stripe_operations').fetchall() == [
                (created['id'], 'CONFIRMED'),
            ]
        code, result = verify(bundle, flow, fake)
        assert code == 0 and result == {'mode': 'stripe-test', 'passed': True, 'problems': []}, result

        # Edit BOTH provider responses, update the manifest checksums, and
        # keep their IDs/status consistent. The projection still rejects it.
        tampered = tmp / 'coordinated-provider-mutation'
        shutil.copytree(bundle, tampered)
        for name in ('stripe_provider_create', 'stripe_provider_retrieval'):
            path = tampered / 'records' / (name + '.json')
            record = read(path)
            record['amount'] += 1
            path.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
        manifest_path = tampered / 'manifest.json'
        manifest = read(manifest_path)
        for name in ('stripe_provider_create', 'stripe_provider_retrieval'):
            relative = 'records/' + name + '.json'
            manifest['files_sha256'][relative] = hashlib.sha256((tampered / relative).read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        code, result = verify(tampered, flow, fake)
        assert code != 0 and not result['passed'] and any(
            'Stripe retrieval differs from authorized projection' in problem
            for problem in result['problems']
        ), result

        for scenario in ('lost', 'missing_id'):
            failure, failure_bundle, failure_calls = run_case(tmp, flow, scenario)
            assert failure.returncode != 0, failure.stdout
            assert str(failure_bundle) in failure.stderr
            check_uncertain(failure_bundle, failure_calls, scenario)

        print(json.dumps({
            'network': 'blocked', 'account_mismatch': 'blocked_before_create',
            'create': 'one_with_permit_key', 'replay': 'zero_extra_provider_calls',
            'retrieve': 'durably_known_id_only', 'lost_and_missing_id': 'uncertain_journals_preserved',
            'fake_bundle': result['mode'], 'fake_bundle_verified': True,
            'coordinated_provider_mutation': 'rejected',
            'stripe_origin': 'NOT_ESTABLISHED',
        }, indent=2))


if __name__ == '__main__':
    main()
