"""Public claim identifiers alone must not authorize settlement or audit reads."""
from importlib import import_module
import json
import logging

from fastapi.testclient import TestClient

from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore
from safeagent_exec_guard.hosted_access import tenant_request_id


def test_settlement_and_audit_require_distinct_capabilities(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    monkeypatch.setenv('SAFEAGENT_SETTLEMENT_SECRET', 's' * 48)
    monkeypatch.setenv('SAFEAGENT_AUDIT_TOKEN', 'a' * 48)
    main = import_module('safeagent.main')
    main._test_ip_counts.clear()
    store = SQLiteExecutionStore(':memory:')
    client = TestClient(main.create_app(store=store))
    body = {'agent_id': 'operator-a', 'action_type': 'payment.collect', 'scope': 'logical-1'}

    first = client.post('/claim/test', json=body).json()
    request_id, token = first['request_id'], first['settlement_token']
    assert first['status'] == 'PROCEED' and len(token) == 64
    assert client.get('/audit').status_code == 403
    assert client.get('/audit', headers={'X-SafeAgent-Audit-Token': 'bad'}).status_code == 403
    assert client.get('/audit', headers={'X-SafeAgent-Audit-Token': 'a' * 48}).status_code == 200

    settle_url = '/settle/' + request_id
    assert client.post(settle_url, json={'result': {'fabricated': True}}).status_code == 403
    assert client.post(settle_url, json={'result': {'fabricated': True}}, headers={
        'X-SafeAgent-Settlement-Token': 'bad',
    }).status_code == 403
    assert store.get(request_id)['status'] == 'PENDING'

    result = {'provider_id': 'pi_authoritatively_read_back'}
    response = client.post(settle_url, json={'result': result}, headers={
        'X-SafeAgent-Settlement-Token': token,
    })
    assert response.status_code == 200 and store.get(request_id)['result'] == result
    no_capability = client.post('/claim/test', json=body).json()
    assert no_capability['status'] == 'SKIP' and 'existing' not in no_capability
    yes_capability = client.post('/claim/test', json=body, headers={
        'X-SafeAgent-Settlement-Token': token,
    }).json()
    assert yes_capability['existing'] == result


def test_unconfigured_settlement_secret_fails_before_claim(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SAFEAGENT_SETTLEMENT_SECRET', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    main = import_module('safeagent.main')
    main._test_ip_counts.clear()
    store = SQLiteExecutionStore(':memory:')
    client = TestClient(main.create_app(store=store))
    response = client.post('/claim/test', json={
        'agent_id': 'operator', 'action_type': 'payment.collect', 'scope': 'one',
    })
    assert response.status_code == 503
    assert store.audit_claims()['total'] == 0


def test_paid_claim_rejects_missing_secret_before_payment_verification(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SAFEAGENT_SETTLEMENT_SECRET', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    main = import_module('safeagent.main')
    store = SQLiteExecutionStore(':memory:')
    app = main.create_app(store=store,
                          payment_address='0x1234567890abcdef1234567890abcdef12345678')
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post('/claim', json={'request_id': 'bad-config', 'action': 'charge'},
                           headers={'x-payment': 'invalid'})
    assert response.status_code == 503
    assert store.get('bad-config') is None


def test_paid_claim_requires_tenant_before_payment_verification(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SAFEAGENT_TENANT_KEYS', raising=False)
    monkeypatch.setenv('SAFEAGENT_SETTLEMENT_SECRET', 's' * 48)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    main = import_module('safeagent.main')
    store = SQLiteExecutionStore(':memory:')
    client = TestClient(main.create_app(
        store=store, payment_address='0x1234567890abcdef1234567890abcdef12345678',
    ), raise_server_exceptions=False)
    response = client.post('/claim', json={'request_id': 'bad-config', 'action': 'charge'},
                           headers={'x-payment': 'invalid'})
    assert response.status_code == 503
    assert store.get('bad-config') is None


def test_hosted_paid_tenants_cannot_preclaim_one_anothers_ids(monkeypatch, tmp_path):
    import httpx
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    monkeypatch.setenv('SAFEAGENT_SETTLEMENT_SECRET', 's' * 48)
    monkeypatch.setenv('SAFEAGENT_AUDIT_TOKEN', 'z' * 48)
    monkeypatch.setenv('SAFEAGENT_TENANT_KEYS', json.dumps({
        'alice': 'a' * 48, 'bob': 'b' * 48,
    }))

    async def unavailable(*args, **kwargs):
        return httpx.Response(503)

    monkeypatch.setattr(httpx.AsyncClient, 'get', unavailable)
    main = import_module('safeagent.main')
    main._test_ip_counts.clear()
    store = SQLiteExecutionStore(':memory:')
    client = TestClient(main.create_app(store=store))
    demo = client.post('/claim/test', json={
        'agent_id': 'demo', 'action_type': 'charge', 'scope': 'predictable',
    }).json()
    demo_id = demo['request_id']
    assert demo_id.startswith('test:')
    body = {'request_id': 'predictable-invoice-1', 'action': 'charge'}
    assert client.post('/claim', json=body).status_code == 403
    a_header = {'X-SafeAgent-Api-Key': 'a' * 48}
    b_header = {'X-SafeAgent-Api-Key': 'b' * 48}
    assert client.post('/claim', json={'request_id': demo_id, 'action': 'charge'},
                       headers=a_header).status_code == 422
    alice = client.post('/claim', json=body, headers=a_header).json()
    bob = client.post('/claim', json=body, headers=b_header).json()
    assert alice['status'] == bob['status'] == 'PROCEED'
    assert store.audit_claims()['total'] == 3
    assert store.get(tenant_request_id('alice', body['request_id']))['status'] == 'PENDING'
    assert client.get('/audit', headers=a_header).json()['total'] == 1
    assert client.get('/audit', headers=b_header).json()['total'] == 1
    proof_url = '/claim/' + tenant_request_id('alice', body['request_id']) + '/proof'
    assert client.get(proof_url).status_code == 403
    assert client.get(proof_url, headers=b_header).status_code == 403
    assert client.post('/sweep').status_code == 403
    assert client.post('/settle/' + body['request_id'], json={'result': {'forged': True}},
                       headers={**b_header, 'X-SafeAgent-Settlement-Token': alice['settlement_token']}
                       ).status_code == 403
    assert store.get(tenant_request_id('bob', body['request_id']))['status'] == 'PENDING'


def test_invalid_body_is_not_logged_and_cors_is_opt_in(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SAFEAGENT_CORS_ORIGINS', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    main = import_module('safeagent.main')
    client = TestClient(main.create_app(store=SQLiteExecutionStore(':memory:')))
    with caplog.at_level(logging.WARNING):
        response = client.post('/claim', content='{"private_key":"DO_NOT_LOG_123"}',
                               headers={'Content-Type': 'application/json',
                                        'Origin': 'https://untrusted.example'})
    assert response.status_code == 422
    assert 'DO_NOT_LOG_123' not in caplog.text
    assert 'access-control-allow-origin' not in response.headers
