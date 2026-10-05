#!/usr/bin/env python3
"""Mercor-shaped wrapper around bounded SafeAgent Control findings.

Prototype only. Based on Mercor's public verifier model; not an official
Mercor integration.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

VALID_RESULTS = {
    "CONFIRMED",
    "MISSING_EXTERNALLY",
    "CONTRADICTION",
    "UNCERTAIN",
}


class VerifierInputError(ValueError):
    """Input does not satisfy the bounded verifier contract."""


def verify_external_effects(report: dict[str, Any]) -> dict[str, Any]:
    findings = report.get("findings")
    if not isinstance(findings, list) or not findings:
        raise VerifierInputError("findings must be a non-empty list")

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            raise VerifierInputError(f"finding[{index}] must be an object")
        result = item.get("control_result")
        if result not in VALID_RESULTS:
            raise VerifierInputError(
                f"finding[{index}] has unsupported control_result={result!r}"
            )
        case = item.get("case", f"finding-{index + 1}")
        normalized.append({"case": str(case), "control_result": result})

    counts = Counter(x["control_result"] for x in normalized)
    total = len(normalized)
    confirmed = counts["CONFIRMED"]
    score = confirmed / total

    blocking = [
        {
            "case": x["case"],
            "control_result": x["control_result"],
        }
        for x in normalized
        if x["control_result"] != "CONFIRMED"
    ]

    count_text = ", ".join(
        f"{name}={counts.get(name, 0)}"
        for name in ("CONFIRMED", "MISSING_EXTERNALLY", "CONTRADICTION", "UNCERTAIN")
    )

    return {
        "schema": "safeagent.mercor-external-effect-verifier.v0",
        "scope": "external_state_changes",
        "score": round(score, 6),
        "passed": not blocking,
        "explanation": (
            f"SafeAgent external-effect reconciliation: {count_text}. "
            "Only authoritative CONFIRMED findings count as success."
        ),
        "blocking_reasons": blocking,
        "replay_authorized": False,
        "prototype": True,
        "official_mercor_integration": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = verify_external_effects(
        json.loads(args.report.read_text(encoding="utf-8"))
    )
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
