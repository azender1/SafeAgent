"""Public claim identifiers alone must not authorize settlement or audit reads."""
from importlib import import_module

from fastapi.testclient import TestClient

from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore


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
