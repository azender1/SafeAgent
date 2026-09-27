"""
Regression fixtures: the two live counterexamples found by property testing
against v0.9, saved verbatim (minimized) BEFORE the disposition/findings
refactor, per explicit instruction. These must keep passing under any
future engine version -- if either ever fails again, that is a real
regression, not a new discovery.

Fixture 1: REJECTED claim's contradiction check colliding with an
unrelated ambiguity among other claims over the same order (the exact
counterexample that motivated the two-layer disposition/findings split).

Fixture 2: reference-model bug -- an order can be consistently assigned to
the same claim across every optimal matching even when the OVERALL
solution isn't unique (a different claim's match varies elsewhere). Only
kept here as a record of the reference model's own bug history; the fix
lives in reference_model.py itself.
"""
from datetime import datetime
from reconcile_v12 import InternalClaim, BrokerOrder, Interpretation, FailureType

T = datetime(2026, 9, 8, 9, 30, 0)

FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY = {
    "claims": [
        InternalClaim("claim-0", "TQQQ", "buy", 3, "base", T, "COMMITTED", Interpretation.SUCCESS,
                       broker_order_id="B"),
        InternalClaim("claim-1", "TQQQ", "buy", 3, "base", T, "ERROR", Interpretation.SUCCESS,
                       broker_order_id="B", failure_type=FailureType.BROKER_REJECTED),
        InternalClaim("claim-2", "TQQQ", "buy", 3, "base", T, "COMMITTED", Interpretation.SUCCESS,
                       broker_order_id="B"),
    ],
    "orders": [
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T),
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T),
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T),
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T, broker_order_id="B"),
    ],
}

FIXTURE_2_CONSISTENT_ORDER_DESPITE_OTHER_AMBIGUITY = {
    "claims": [
        InternalClaim("claim-0", "TQQQ", "buy", 3, "base", T, "COMMITTED", Interpretation.SUCCESS,
                       client_order_id="B"),
        InternalClaim("claim-1", "TQQQ", "buy", 3, "base", T, "COMMITTED", Interpretation.SUCCESS),
    ],
    "orders": [
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T),
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T),
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T),
        BrokerOrder("TQQQ", "buy", 3, "filled", T, T, client_order_id="B"),
    ],
}