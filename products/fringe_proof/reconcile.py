"""Local, read-only comparison of payroll, union remittance, and fund receipts.

This is an evidence tool, not a payroll calculator or a compliance certification.
Only customer-approved rate tables are used; missing or ambiguous rules are findings.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

CENT = Decimal("0.01")
FIELDS = {
    "payroll": ("employee_id", "work_date", "local", "classification", "covered_hours"),
    "rates": ("local", "classification", "fund", "effective_from", "effective_to", "rate_per_hour"),
    "remittance": ("employee_id", "local", "classification", "fund", "reported_hours", "reported_amount"),
    "fund_ack": ("local", "fund", "amount_received"),
}


def day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {value!r}") from exc


def number(value: str, label: str, *, signed: bool = False) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    if not result.is_finite() or (not signed and result < 0):
        raise ValueError(f"invalid {label}: {value!r}")
    return result


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def load(path: Path, kind: str) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"{path}: missing or duplicate CSV headers")
        missing = set(FIELDS[kind]) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        rows = []
        for line, row in enumerate(reader, start=2):
            if None in row or any(not row[field].strip() for field in FIELDS[kind]
                                      if field != "effective_to"):
                raise ValueError(f"{path}:{line}: missing value or extra column")
            rows.append({key: value.strip() for key, value in row.items()})
        return rows


def compare(payroll: list[dict[str, str]], rates: list[dict[str, str]],
            remittance: list[dict[str, str]], acknowledgments: list[dict[str, str]],
            start: date, end: date) -> list[dict[str, str]]:
    if start > end:
        raise ValueError("period start is after end")
    findings: list[dict[str, str]] = []
    for kind, rows in (("payroll", payroll), ("rates", rates),
                       ("remittance", remittance), ("fund_ack", acknowledgments)):
        if not rows:
            findings.append({"type": "EMPTY_SOURCE", "source": kind})
    rules: dict[tuple[str, str, str], list[tuple[date, date | None, Decimal]]] = defaultdict(list)
    for row in rates:
        begin = day(row["effective_from"])
        finish = day(row["effective_to"]) if row["effective_to"] else None
        if finish is not None and finish < begin:
            raise ValueError("rate effective_to precedes effective_from")
        key = row["local"], row["classification"], row["fund"]
        rules[key].append((begin, finish, number(row["rate_per_hour"], "rate")))

    # Keep exact decimal values until the employee/fund period total is calculated.
    expected: dict[tuple[str, str, str, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for row in payroll:
        work_date = day(row["work_date"])
        if not start <= work_date <= end:
            raise ValueError(f"payroll work_date {work_date} outside selected period")
        hours = number(row["covered_hours"], "covered_hours")
        base = row["employee_id"], row["local"], row["classification"]
        candidate_funds = sorted({fund for local, classification, fund in rules
                                  if (local, classification) == base[1:]})
        if not candidate_funds:
            findings.append({"type": "NO_RATE", "employee_id": base[0],
                             "local": base[1], "classification": base[2], "work_date": str(work_date)})
        for fund in candidate_funds:
            active = [rate for begin, finish, rate in rules[base[1], base[2], fund]
                      if begin <= work_date and (finish is None or work_date <= finish)]
            if len(active) != 1:
                findings.append({"type": "AMBIGUOUS_RATE" if active else "NO_RATE",
                                 "employee_id": base[0], "local": base[1],
                                 "classification": base[2], "fund": fund, "work_date": str(work_date)})
                continue
            totals = expected[(*base, fund)]
            totals[0] += hours
            totals[1] += hours * active[0]

    reported: dict[tuple[str, str, str, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for row in remittance:
        key = tuple(row[field] for field in ("employee_id", "local", "classification", "fund"))
        reported[key][0] += number(row["reported_hours"], "reported_hours", signed=True)
        reported[key][1] += number(row["reported_amount"], "reported_amount", signed=True)
    for key in sorted(expected.keys() | reported.keys()):
        labels = dict(zip(("employee_id", "local", "classification", "fund"), key))
        if key not in expected:
            findings.append({"type": "UNMATCHED_REMITTANCE", **labels})
        elif key not in reported:
            findings.append({"type": "MISSING_REMITTANCE", **labels,
                             "expected_hours": str(expected[key][0]),
                             "expected_amount": money(expected[key][1])})
        else:
            if expected[key][0] != reported[key][0]:
                findings.append({"type": "HOURS_DELTA", **labels,
                                 "payroll_hours": str(expected[key][0]),
                                 "reported_hours": str(reported[key][0])})
            if money(expected[key][1]) != money(reported[key][1]):
                findings.append({"type": "AMOUNT_DELTA", **labels,
                                 "expected_amount": money(expected[key][1]),
                                 "reported_amount": money(reported[key][1])})

    # A fund-level receipt proves an aggregate amount only, never a worker allocation.
    received: dict[tuple[str, str], Decimal] = {}
    for row in acknowledgments:
        key = row["local"], row["fund"]
        if key in received:
            findings.append({"type": "DUPLICATE_FUND_ACK", "local": key[0], "fund": key[1]})
            continue
        received[key] = number(row["amount_received"], "amount_received")
    submitted: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for (_, local, _, fund), (_, amount) in reported.items():
        submitted[local, fund] += amount
    for key in sorted(submitted.keys() | received.keys()):
        labels = {"local": key[0], "fund": key[1]}
        if key not in submitted:
            findings.append({"type": "UNMATCHED_FUND_ACK", **labels})
        elif key not in received:
            findings.append({"type": "MISSING_FUND_ACK", **labels})
        elif money(submitted[key]) != money(received[key]):
            findings.append({"type": "FUND_AMOUNT_DELTA", **labels,
                             "submitted_amount": money(submitted[key]),
                             "received_amount": money(received[key])})
    return sorted(findings, key=lambda finding: json.dumps(finding, sort_keys=True))


def run(paths: dict[str, Path], start: date, end: date) -> dict:
    sources = {kind: load(path, kind) for kind, path in paths.items()}
    findings = compare(sources["payroll"], sources["rates"], sources["remittance"],
                       sources["fund_ack"], start, end)
    report = {
        "product": "SafeAgent Control: Fringe Proof",
        "scope": "comparison of provided records only; not a compliance certification",
        "period": {"start": str(start), "end": str(end)},
        "input_sha256": {kind: hashlib.sha256(path.read_bytes()).hexdigest()
                         for kind, path in sorted(paths.items())},
        "input_rows": {kind: len(rows) for kind, rows in sorted(sources.items())},
        "status": "EXCEPTIONS" if findings else "MATCHED_TO_PROVIDED_RECORDS",
        "findings": findings,
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for kind in FIELDS:
        parser.add_argument("--" + kind.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--period-start", type=day, required=True)
    parser.add_argument("--period-end", type=day, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {kind: getattr(args, kind) for kind in FIELDS}
    try:
        result = run(paths, args.period_start, args.period_end)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "findings": len(result["findings"]),
                      "report": str(args.output)}))
    return 0 if not result["findings"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
