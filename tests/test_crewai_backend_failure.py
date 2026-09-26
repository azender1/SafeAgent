"""Hosted cache errors must never turn into permission to execute."""
from importlib import util
from pathlib import Path

import pytest
import requests


def test_claim_failure_does_not_admit_external_action(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'examples/crewai/safeagent_backend.py'
    spec = util.spec_from_file_location('crewai_safeagent_backend', path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    backend = module.SafeAgentCacheBackend(retries=1)

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError('unreachable')

    monkeypatch.setattr(module.requests, 'request', unreachable)
    with pytest.raises(RuntimeError, match='request failed'):
        backend.claim_if_absent('payment:1', object())
