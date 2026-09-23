#!/usr/bin/env python3
"""Offline unit checks of the proposed Stripe Test Mode adapter; no HTTP calls."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract import ACCOUNT
from run_fixture import StripeTestProvider


class FakePaymentIntents:
    def __init__(self):
        self.creates = []
        self.retrieves = []

    def create(self, params, options):
        self.creates.append((params, options))
        return {'id': 'pi_test_review_only', 'object': 'payment_intent',
                'status': 'succeeded', 'amount': params['amount'], 'amount_received': params['amount'],
                'currency': params['currency'], 'payment_method': params['payment_method'],
                'metadata': params['metadata'], 'livemode': False,
                'client_secret': 'must_not_be_recorded'}

    def retrieve(self, payment_intent_id):
        self.retrieves.append(payment_intent_id)
        return self.create({'amount': 100, 'currency': 'usd', 'payment_method': 'pm_card_visa',
                            'metadata': {'source': 'test'}}, {'idempotency_key': 'fake'})


class FakeClient:
    account_id = ACCOUNT
    calls = []
    intents = FakePaymentIntents()

    def __init__(self, key, max_network_retries):
        assert key == 'sk_test_review_only' and max_network_retries == 0
        self.v1 = SimpleNamespace(payment_intents=self.intents)

    def raw_request(self, method, endpoint):
        self.calls.append((method, endpoint))
        return SimpleNamespace(data={'id': self.account_id, 'object': 'account'})


def main():
    sys.modules['stripe'] = SimpleNamespace(StripeClient=FakeClient)
    try:
        StripeTestProvider('sk_live_refused')
        raise AssertionError('live key accepted')
    except ValueError:
        pass
    FakeClient.account_id = 'acct_WRONG'
    try:
        StripeTestProvider('sk_test_review_only')
        raise AssertionError('wrong account accepted')
    except ValueError:
        pass
    assert FakeClient.intents.creates == []
    FakeClient.account_id = ACCOUNT
    provider = StripeTestProvider('sk_test_review_only')
    assert FakeClient.calls == [('get', '/v1/account'), ('get', '/v1/account')]
    params = {'amount': 100, 'currency': 'usd', 'payment_method': 'pm_card_visa',
              'metadata': {'source': 'test'}, 'idempotency_key': 'safeagent:permit-review'}
    created = provider.create(**params)
    fetched = provider.retrieve(created['id'])
    assert FakeClient.intents.creates[0][1] == {'idempotency_key': 'safeagent:permit-review'}
    assert FakeClient.intents.retrieves == [created['id']]
    assert created == fetched and 'client_secret' not in created
    print('live key refused; wrong account blocked before create; known PaymentIntent retrieved; secret excluded')


if __name__ == '__main__':
    main()
