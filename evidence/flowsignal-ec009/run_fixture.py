#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any


FLOW_COMMIT = "471d8af596044d61f86f774f70cd8c6ff2efc31c"
SAFEAGENT_COMMIT = "b8a2fa6baa98f0d469e3f3787b420648596295d0"
SAFEAGENT_RELEASE = "v0.1.25"
TEST_PRIVATE_KEY_HEX = "11" * 32


def plain(value: Any) -> Any:
    if hasattr(value, "to_dict_recursive"):
        value = value.to_dict_recursive()
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plain(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


class SimulatedStripe:
    """One-process test double; never represented as external Stripe evidence."""

    def __init__(self) -> None:
        self.created: dict[str, dict[str, Any]] = {}
        self.create_calls = 0
        self.retrieve_calls = 0

    def create(self, **params: Any) -> dict[str, Any]:
        self.create_calls += 1
        key = str(params.pop("idempotency_key"))
        if key not in self.created:
            self.created[key] = {
                "id": "pi_simulated_" + hashlib.sha256(key.encode()).hexdigest()[:24],
                "object": "payment_intent",
                "status": "succeeded",
                **params,
            }
        return dict(self.created[key])

    def retrieve(self, payment_intent_id: str) -> dict[str, Any]:
        self.retrieve_calls += 1
        return next(dict(v) for v in self.created.values() if v["id"] == payment_intent_id)


def build_manifest(root: Path, mode: str) -> dict[str, Any]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema": "safeagent.flowsignal-ec009-evidence.v1",
        "mode": mode,
        "claim_scope": "gateway-controlled path only",
        "flowsignal_commit": FLOW_COMMIT,
        "safeagent_release": SAFEAGENT_RELEASE,
        "safeagent_commit": SAFEAGENT_COMMIT,
        "files_sha256": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flowsignal-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("simulated", "stripe-test"), default="simulated")
    parser.add_argument("--output", type=Path, default=Path("evidence/latest"))
    args = parser.parse_args()

    flow_root = args.flowsignal_root.resolve()
    harness_root = flow_root / "harness"
    if not (harness_root / "app" / "engines" / "execution_gateway.py").exists():
        raise SystemExit("--flowsignal-root is not a FlowSignal repository checkout")

    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    runtime = output / "runtime"
    runtime.mkdir()
    os.environ["FLOWSIGNAL_PERMIT_CONSUMPTION_STORE"] = str(runtime / "flowsignal-permits.db")
    os.environ["FLOWSIGNAL_CONSEQUENCE_OUTCOME_STORE"] = str(runtime / "flowsignal-outcomes.db")
    os.environ["FLOWSIGNAL_ROLLBACK_ANCHOR_STORE"] = str(runtime / "flowsignal-anchor.db")

    sys.path.insert(0, str(harness_root))
    from app.engines.execution_gateway import ExecutionAttempt, action_binding_hash, validate_execution
    from app.engines.financial_runtime import evaluate_financial
    from app.engines.permit_authority import verify_execution_permit
    from harness.runner import load_scenario

    from safeagent_exec_guard import (
        ActionRequest,
        BoundaryGateway,
        PermitAuthority,
        PermitDenied,
        SQLitePermitStore,
        SQLiteStripeStore,
        StripePaymentIntentGateway,
    )
    from safeagent_exec_guard.boundary import verify_permit

    base = load_scenario(harness_root / "harness" / "scenarios" / "AP-001_allow.json")
    flow_request = replace(
        base,
        scenario_id="EC-009-SAFEAGENT-STRIPE-001",
        amount=1.0,
        currency="GBP",
        purpose="FlowSignal EC-009 SafeAgent Stripe Test Mode fixture",
    )
    response, authority_receipt = evaluate_financial(flow_request)
    if response.decision != "ALLOW":
        raise SystemExit(f"FlowSignal did not ALLOW fixture: {response.decision}")
    attempt = ExecutionAttempt(
        actor_id=flow_request.actor_id,
        principal_id=flow_request.principal_id,
        action=flow_request.action,
        target=flow_request.target,
        amount=flow_request.amount,
        currency=flow_request.currency,
        source_account=flow_request.source_account,
        beneficiary=flow_request.beneficiary,
        purpose=flow_request.purpose,
        mandate_id=flow_request.mandate_id,
        attempted_at=flow_request.requested_execution_time,
    )
    gateway_result = validate_execution(authority_receipt, attempt)
    flow_permit = gateway_result.execution_permit
    if gateway_result.status != "PERMITTED" or flow_permit is None:
        raise SystemExit(f"FlowSignal gateway did not permit fixture: {gateway_result.reason_code}")
    if not verify_execution_permit(flow_permit):
        raise SystemExit("FlowSignal permit verification failed")
    flow_binding = action_binding_hash(attempt)
    if flow_binding != flow_permit.action_binding_hash:
        raise SystemExit("FlowSignal action binding mismatch")

    safe_payload = {
        "amount": 100,
        "currency": "usd",
        "payment_method_types": ["card"],
        "payment_method": "pm_card_visa",
        "confirm": True,
        "description": "FlowSignal EC-009 via SafeAgent",
        "metadata": {
            "flowsignal_authority_receipt_id": authority_receipt.id,
            "flowsignal_action_binding_hash": flow_binding,
            "flowsignal_permit_signature_sha256": hashlib.sha256(flow_permit.signature.encode()).hexdigest(),
        },
    }
    safe_request = ActionRequest(
        principal=flow_request.principal_id,
        run_id="flowsignal-ec009-" + authority_receipt.id,
        action="stripe.payment_intent.create",
        target="stripe:test-payment",
        payload=safe_payload,
    )
    safe_authority = PermitAuthority.from_private_key_hex(
        TEST_PRIVATE_KEY_HEX, issuer="flowsignal-safeagent-ec009-fixture"
    )
    safe_permit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "flowsignal:" + flow_permit.signature))
    safe_token = safe_authority.issue(safe_request, ttl_seconds=300, permit_id=safe_permit_id)
    safe_claims = verify_permit(safe_token, safe_authority.public_key_hex())
    permit_store = SQLitePermitStore(runtime / "safeagent-permits.db")
    stripe_store = SQLiteStripeStore(runtime / "safeagent-stripe.db")
    safe_boundary = BoundaryGateway(safe_authority.public_key_hex(), permit_store)

    simulated = None
    if args.mode == "simulated":
        simulated = SimulatedStripe()
        stripe_gateway = StripePaymentIntentGateway(safe_boundary, simulated, stripe_store)
    else:
        api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not api_key.startswith("sk_test_"):
            raise SystemExit("stripe-test mode requires STRIPE_SECRET_KEY=sk_test_...")
        stripe_gateway = StripePaymentIntentGateway.from_api_key(safe_boundary, api_key, stripe_store)

    first = stripe_gateway.dispatch(safe_token, safe_request)
    try:
        stripe_gateway.dispatch(safe_token, safe_request)
        replay = {"decision": "ERROR", "reason": "duplicate dispatch allowed"}
    except PermitDenied as exc:
        replay = {"decision": "BLOCKED", "reason": exc.reason}
    observation = stripe_gateway.reconcile(first.permit_id)

    write_json(output / "records" / "flowsignal_authority_request.json", flow_request)
    write_json(output / "records" / "flowsignal_authority_response.json", response)
    write_json(output / "records" / "flowsignal_authority_receipt.json", authority_receipt)
    write_json(output / "records" / "flowsignal_gateway_result.json", gateway_result)
    write_json(output / "records" / "flowsignal_execution_permit.json", flow_permit)
    write_json(output / "records" / "safeagent_action_request.json", safe_request)
    write_json(output / "records" / "safeagent_permit_claims.json", safe_claims)
    write_json(output / "records" / "safeagent_first_dispatch.json", first)
    write_json(output / "records" / "safeagent_replay_attempt.json", replay)
    write_json(output / "records" / "safeagent_boundary_record.json", permit_store.get(first.permit_id))
    write_json(output / "records" / "safeagent_boundary_events.json", permit_store.events(first.permit_id))
    write_json(output / "records" / "stripe_operation.json", stripe_store.get(first.permit_id))
    write_json(output / "records" / "stripe_retrieval.json", observation)
    write_json(
        output / "records" / "binding.json",
        {
            "flowsignal_authority_receipt_id": authority_receipt.id,
            "flowsignal_action_binding_hash": flow_binding,
            "flowsignal_permit_signature_sha256": hashlib.sha256(flow_permit.signature.encode()).hexdigest(),
            "safeagent_permit_id": first.permit_id,
            "safeagent_payload_sha256": safe_request.payload_sha256,
            "stripe_payment_intent_id": observation.payment_intent_id,
        },
    )
    if simulated is not None:
        write_json(
            output / "records" / "simulated_provider_calls.json",
            {
                "label": "SIMULATED_NOT_STRIPE_EVIDENCE",
                "create_calls": simulated.create_calls,
                "retrieve_calls": simulated.retrieve_calls,
                "objects": list(simulated.created.values()),
            },
        )

    summary = {
        "schema": "safeagent.flowsignal-ec009-summary.v1",
        "mode": args.mode,
        "flowsignal_decision": response.decision,
        "flowsignal_gateway": gateway_result.status,
        "flowsignal_permit_valid": True,
        "safeagent_first_dispatch": first.decision,
        "safeagent_replay": replay,
        "provider_state": observation.state,
        "provider_source": observation.source,
        "payment_intent_id": observation.payment_intent_id,
        "supported_claim": (
            args.mode == "stripe-test"
            and first.decision == "SETTLED"
            and replay == {"decision": "BLOCKED", "reason": "permit_already_consumed"}
            and observation.state == "CONFIRMED"
        ),
        "limitations": [
            "gateway-controlled path only",
            "no production settlement claim",
            "no alternate-route closure claim",
            "no deployment-wide non-bypassability claim",
            "simulated mode is not external Stripe evidence",
        ],
    }
    write_json(output / "summary.json", summary)
    write_json(output / "manifest.json", build_manifest(output, args.mode))
    print(json.dumps(summary, indent=2))
    return 0 if replay["decision"] == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
