"""The public test claim must never fabricate a result for unresolved work."""
from importlib import import_module

from fastapi.testclient import TestClient

from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore


def test_duplicate_pending_stays_pending_until_real_settlement(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    monkeypatch.setenv('SAFEAGENT_SETTLEMENT_SECRET', 's' * 48)
    main = import_module('safeagent.main')
    main._test_ip_counts.clear()
    store = SQLiteExecutionStore(':memory:')
    client = TestClient(main.create_app(store=store))
    request = {'agent_id': 'operator-a', 'action_type': 'payment.collect', 'scope': 'logical-1'}

    first = client.post('/claim/test', json=request)
    assert first.status_code == 200 and first.json()['status'] == 'PROCEED'
    request_id = first.json()['request_id']
    second = client.post('/claim/test', json=request)
    assert second.status_code == 200 and second.json()['status'] == 'PENDING'
    assert second.json()['request_id'] == request_id
    assert store.get(request_id)['status'] == 'PENDING'
    assert store.get(request_id)['result'] is None

    actual_result = {'provider_id': 'pi_authoritatively_read_back'}
    store.settle(request_id, actual_result)
    third = client.post('/claim/test', json=request, headers={
        'X-SafeAgent-Settlement-Token': first.json()['settlement_token'],
    })
    assert third.json()['status'] == 'SKIP'
    assert third.json()['existing'] == actual_result


def test_concurrent_claim_loser_reports_pending(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('SAFEAGENT_DB_PATH', str(tmp_path / 'import-only.db'))
    monkeypatch.setenv('SAFEAGENT_SETTLEMENT_SECRET', 's' * 48)
    main = import_module('safeagent.main')
    main._test_ip_counts.clear()
    store = SQLiteExecutionStore(':memory:')
    original_get = store.get
    raced = False

    def competing_get(request_id):
        nonlocal raced
        if not raced:
            raced = True
            assert store.claim(request_id, 'payment.collect')
            return None
        return original_get(request_id)

    monkeypatch.setattr(store, 'get', competing_get)
    client = TestClient(main.create_app(store=store))
    result = client.post('/claim/test', json={
        'agent_id': 'operator-b', 'action_type': 'payment.collect', 'scope': 'logical-2',
    })
    assert result.status_code == 200 and result.json()['status'] == 'PENDING'
    assert original_get(result.json()['request_id'])['status'] == 'PENDING'
