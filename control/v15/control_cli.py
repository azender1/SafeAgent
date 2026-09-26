#!/usr/bin/env python3
"""
control_cli.py -- reconciliation CLI over SafeAgent claims and Alpaca orders.

    python control_cli.py reconcile \\
        --claims <safeagent-file> --orders <alpaca-file> \\
        --as-of <ISO timestamp> --output-dir <directory>

CORE IS FROZEN: this file only calls reconcile_with_disposition() and reads
its outputs. It never reimplements matching, assignment, disposition, or
reason-code logic.

Exit codes:
  0 -- processing succeeded (regardless of how many discrepancies were found)
  2 -- a file could not be read or parsed in a recognized format
  1 -- any other processing/configuration failure

This tool never prints API keys, credentials, or backend URLs -- it never
reads any (file-based ingestion only; see README for the optional,
unimplemented-by-default live-fetch note).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from reconcile_v12 import OrderDisposition, Result, ReasonCode, InternalClaim
from reconcile_v12_classify import reconcile_with_disposition

from safeagent_claims_parser import parse_claims
from safeagent_db_adapter import parse_safeagent_db
from alpaca_orders_parser import parse_and_normalize_orders


# ----------------------------- severity model -----------------------------
# Not part of the core: a CLI-level presentation choice for which findings
# an operator should look at first. Does not alter any core semantics.

RESULT_SEVERITY = {
    Result.CONTRADICTION.value: "CRITICAL",
    Result.UNCERTAIN.value: "MEDIUM",
    Result.MISSING_EXTERNALLY.value: "MEDIUM",
    Result.UNRECORDED_INTERNALLY.value: "MEDIUM",
    Result.REJECTED.value: "LOW",
    Result.CONFIRMED.value: "INFO",
    Result.DUPLICATE_BLOCKED.value: "INFO",
}
# A handful of reason codes escalate their finding's severity regardless of
# the base Result -- an identifier conflict is worse than a generic
# "uncertain" even though both currently map to Result.UNCERTAIN.
REASON_CODE_ESCALATION = {
    ReasonCode.IDENTIFIER_CONFLICT.value: "HIGH",
    ReasonCode.EXTERNAL_IDENTIFIER_CONFLICT.value: "HIGH",
    ReasonCode.REJECTION_EXECUTION_CONTRADICTION.value: "CRITICAL",
    ReasonCode.CALLER_IGNORED_SKIP.value: "HIGH",  # the September SKIP-mislogged pattern
}
INGESTION_FINDING_SEVERITY = {
    "SOURCE_IDENTITY_CONFLICT": "HIGH",
    "UNMAPPED_STATUS_UNRESOLVED": "MEDIUM",
}
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _severity_for_result(result_value: str, reason_codes: tuple) -> str:
    sev = RESULT_SEVERITY.get(result_value, "MEDIUM")
    for rc in reason_codes:
        rc_val = rc.value if hasattr(rc, "value") else rc
        if rc_val in REASON_CODE_ESCALATION:
            escalated = REASON_CODE_ESCALATION[rc_val]
            if _SEVERITY_RANK[escalated] < _SEVERITY_RANK[sev]:
                sev = escalated
    return sev


# ----------------------------- finding assembly -----------------------------

def build_findings(results, disposition, orders, ingestion_findings, order_provenance_by_id,
                    interpretation_source_by_claim_id=None) -> list[dict]:
    interpretation_source_by_claim_id = interpretation_source_by_claim_id or {}
    findings = []

    for r in results:
        reason_codes = tuple(rc.value for rc in r.reason_codes)
        severity = _severity_for_result(r.result.value, r.reason_codes)
        source_ids = []
        if r.claim is not None:
            if r.claim.broker_order_id:
                source_ids.append(f"broker_order_id={r.claim.broker_order_id}")
            if r.claim.client_order_id:
                source_ids.append(f"client_order_id={r.claim.client_order_id}")
        order_provenance = None
        if r.matched_broker_order is not None:
            prov = order_provenance_by_id.get(id(r.matched_broker_order))
            if prov is not None:
                order_provenance = {"source_file": prov.source_file, "record_number": prov.record_number}
        findings.append({
            "claim_stable_id": r.claim.stable_id if r.claim else None,
            "claim_request_id": r.claim.request_id if r.claim else None,
            "order_stable_id": r.matched_broker_order.stable_id if r.matched_broker_order else None,
            "result": r.result.value,
            "reason_codes": reason_codes,
            "severity": severity,
            "explanation": r.evidence,
            "source_identifiers": source_ids,
            "involved_order_stable_ids": list(r.involved_order_stable_ids),
            "correlation_id": r.correlation_id,
            "accounting_category": r.accounting_category.value if r.accounting_category else None,
            "order_provenance": order_provenance,
            "caller_interpretation": r.claim.caller_interpretation.value if r.claim else None,
            "caller_interpretation_source": (
                interpretation_source_by_claim_id.get(id(r.claim)) if r.claim else None
            ),
        })

    for f in ingestion_findings:
        severity = INGESTION_FINDING_SEVERITY.get(f.finding_type, "MEDIUM")
        conflicting_provenance = []
        for rec in f.conflicting_records:
            prov = order_provenance_by_id.get(id(rec))
            conflicting_provenance.append({
                "stable_id": rec.stable_id,
                "source_file": prov.source_file if prov else None,
                "record_number": prov.record_number if prov else None,
            })
        findings.append({
            "claim_stable_id": None,
            "claim_request_id": None,
            "order_stable_id": f.stable_id,
            "result": f"INGESTION_{f.finding_type}",
            "reason_codes": (f.finding_type,),
            "severity": severity,
            "explanation": f.detail,
            "source_identifiers": [],
            "involved_order_stable_ids": [rec.stable_id for rec in f.conflicting_records],
            "correlation_id": None,
            "accounting_category": None,
            "order_provenance": conflicting_provenance,
        })

    findings.sort(key=lambda x: _SEVERITY_RANK.get(x["severity"], 9))
    return findings


# ----------------------------- report writers -----------------------------

def write_json_report(path: Path, summary: dict, findings: list[dict], rejections: dict) -> None:
    payload = {"summary": summary, "findings": findings, "rejections": rejections}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def write_findings_csv(path: Path, findings: list[dict]) -> None:
    columns = [
        "severity", "result", "reason_codes", "claim_stable_id", "claim_request_id",
        "order_stable_id", "explanation", "source_identifiers",
        "involved_order_stable_ids", "correlation_id", "accounting_category",
        "caller_interpretation", "caller_interpretation_source", "order_provenance",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in findings:
            out = dict(row)
            for k in ("reason_codes", "source_identifiers", "involved_order_stable_ids", "order_provenance"):
                out[k] = json.dumps(out[k], default=str)
            writer.writerow({k: out.get(k) for k in columns})


def write_summary_txt(path: Path, summary: dict, findings: list[dict]) -> None:
    lines = []
    lines.append("SafeAgent Control -- Reconciliation Summary")
    lines.append("=" * 44)
    lines.append(f"As-of: {summary['as_of']}")
    lines.append("")
    if "alpaca_api_retrieval" in summary:
        rm = summary["alpaca_api_retrieval"]
        lines.append("ALPACA LIVE RETRIEVAL")
        lines.append(f"  Environment: {rm['environment'].upper()}")
        lines.append(f"  Window: {rm['after']} -> {rm['until']}")
        lines.append(f"  Retrieved at: {rm['retrieved_at']}")
        lines.append(f"  Raw orders retrieved: {rm['raw_orders_retrieved']}")
        lines.append(f"  Dropped (missing required fields): {rm['orders_dropped_missing_required_fields']}")
        lines.append(f"  Pagination complete: {rm['pagination_complete']}")
        lines.append(f"  Status distribution: {rm['status_distribution']}")
        lines.append("")
    lines.append("INPUT VOLUME")
    lines.append(f"  Raw claim records received:   {summary['raw_claims_received']}")
    lines.append(f"  Claim records accepted:       {summary['claims_accepted']}")
    lines.append(f"  Claim records rejected:       {summary['claims_rejected']}")
    if summary.get("claims_outside_retrieval_window", 0) or "alpaca_api_retrieval" in summary:
        lines.append(f"  Claims outside retrieval window (excluded, not reconciled): "
                     f"{summary['claims_outside_retrieval_window']}")
    lines.append(f"  Raw broker records received:  {summary['raw_orders_received']}")
    lines.append(f"  Broker records accepted:      {summary['orders_accepted']}")
    lines.append(f"  Broker records rejected:      {summary['orders_rejected']}")
    lines.append(f"  Broker records after normalization (consolidated set): {summary['orders_after_normalization']}")
    lines.append(f"  Broker records preserved as distinct duplicates (multiplicity, no source id): "
                 f"{summary['orders_preserved_as_duplicates']}")
    lines.append("")
    lines.append("ORDER DISPOSITION")
    for k, v in summary["order_disposition_counts"].items():
        lines.append(f"  {k:<20} {v}")
    lines.append("")
    lines.append("CLAIM / FINDING RESULT COUNTS")
    for k, v in summary["result_counts"].items():
        lines.append(f"  {k:<20} {v}")
    lines.append("")
    lines.append("INGESTION FINDINGS")
    for k, v in summary["ingestion_finding_counts"].items():
        lines.append(f"  {k:<28} {v}")
    if not summary["ingestion_finding_counts"]:
        lines.append("  (none)")
    lines.append("")
    lines.append("WHAT TO INVESTIGATE FIRST")
    top = [f for f in findings if f["severity"] in ("CRITICAL", "HIGH")][:10]
    if not top:
        lines.append("  No CRITICAL or HIGH severity findings.")
    else:
        for f in top:
            ident = f["claim_request_id"] or f["order_stable_id"] or "(standalone)"
            lines.append(f"  [{f['severity']}] {f['result']} -- {ident}: {f['explanation']}")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ----------------------------- main reconcile flow -----------------------------

def _load_orders(
    orders_path: Optional[str], source_account: Optional[str],
    alpaca_api: bool, paper: bool, after: Optional[datetime], until: Optional[datetime],
):
    """
    Returns (clean_orders, ingestion_findings, rejections, provenance_by_id,
    retrieval_metadata). retrieval_metadata is None for the file path --
    only the live API path has retrieval metadata to report.

    AlpacaRetrievalError propagates uncaught to main()'s handler, which
    already treats any exception as a clear, nonzero-exit failure -- see
    that function. This is deliberate: a failed live retrieval must never
    be swallowed here and silently replaced with an empty or partial
    order set presented as complete.
    """
    if alpaca_api:
        from alpaca_api_adapter import build_client, fetch_and_normalize_orders, AlpacaRetrievalError
        print(f"Alpaca environment: {'PAPER' if paper else 'LIVE'}")
        print(f"Retrieval window: {after.isoformat()} -> {until.isoformat()}")
        client = build_client(paper=paper)
        clean_orders, ingestion_findings, order_rejections, order_provenance_by_id, retrieval_metadata, complete = \
            fetch_and_normalize_orders(client, after, until, default_source_account=source_account)
        if not complete:
            raise AlpacaRetrievalError(
                "Alpaca order retrieval did not complete within the pagination safety limit -- "
                "refusing to reconcile against a partial result presented as complete. "
                "Narrow --after/--until and retry."
            )
        retrieval_metadata["environment"] = "paper" if paper else "live"
        return clean_orders, ingestion_findings, order_rejections, order_provenance_by_id, retrieval_metadata
    else:
        clean_orders, ingestion_findings, order_rejections, order_provenance_by_id = \
            parse_and_normalize_orders(orders_path, default_source_account=source_account)
        return clean_orders, ingestion_findings, order_rejections, order_provenance_by_id, None


def run_reconcile(
    claims_path: str, orders_path: Optional[str], as_of: datetime, output_dir: Path,
    source_account: Optional[str] = None, source_system: Optional[str] = None,
    bot_log_path: Optional[str] = None,
    alpaca_api: bool = False, paper: bool = True,
    after: Optional[datetime] = None, until: Optional[datetime] = None,
) -> int:
    lower = claims_path.lower()
    if lower.endswith((".db", ".sqlite", ".sqlite3")):
        claims, claim_rejections, claim_provenance = parse_safeagent_db(
            claims_path, source_account=source_account, bot_log_path=bot_log_path,
        )
        if source_system:
            # decode_request_id() only recovers strategy/source_system from
            # the CURRENT (STRATEGY_ID-keyed) request_id shapes -- older
            # rows have none embedded at all (see safeagent_db_adapter.py's
            # module docstring). --source-system backfills it uniformly
            # ONLY where the database itself provided nothing, so a value
            # actually decoded from the request_id is never overwritten by
            # a CLI default guessing something more specific.
            claims = [
                c if c.source_system else InternalClaim(
                    request_id=c.request_id, symbol=c.symbol, side=c.side, qty=c.qty,
                    stage=c.stage, claimed_at=c.claimed_at, outcome=c.outcome,
                    caller_interpretation=c.caller_interpretation,
                    broker_order_id=c.broker_order_id, client_order_id=c.client_order_id,
                    failure_type=c.failure_type,
                    source_system=source_system, source_account=c.source_account,
                )
                for c in claims
            ]
    else:
        claims, claim_rejections, claim_provenance = parse_claims(claims_path)
    raw_claims_received = len(claims) + len(claim_rejections)

    # In --alpaca-api mode, claims are loaded from the WHOLE SafeAgent
    # database with no date filter, but orders are only ever retrieved for
    # [after, until]. Without this filter, any claim outside that window
    # has no order to match against (not because the order is missing --
    # because it was never fetched) and was previously misclassified as
    # MISSING_EXTERNALLY/UNCERTAIN. Found via a real acceptance run: 6 of 7
    # MISSING_EXTERNALLY findings were claims from a date the retrieval
    # window didn't cover at all, not genuine discrepancies. Scoped to
    # --alpaca-api only -- the file-based --orders path has no defined
    # retrieval window to compare against, so it's left exactly as before.
    # Boundary is INCLUSIVE on both ends (a claim timestamped exactly at
    # --after or --until is in scope); a claim whose timestamp isn't
    # timezone-aware can't be safely compared and is excluded rather than
    # guessed. claim_provenance is filtered in lockstep so it stays
    # index-aligned with claims for the interpretation-source lookup below.
    claims_outside_window_count = 0
    if alpaca_api:
        filtered_claims, filtered_provenance = [], []
        for c, p in zip(claims, claim_provenance):
            ca = c.claimed_at
            in_window = (
                ca.tzinfo is not None and after.tzinfo is not None and until.tzinfo is not None
                and after <= ca <= until
            )
            if in_window:
                filtered_claims.append(c)
                filtered_provenance.append(p)
            else:
                claims_outside_window_count += 1
        claims, claim_provenance = filtered_claims, filtered_provenance

    clean_orders, ingestion_findings, order_rejections, order_provenance_by_id, retrieval_metadata = \
        _load_orders(orders_path, source_account, alpaca_api, paper, after, until)
    raw_orders_received = len(order_provenance_by_id) + len(order_rejections)
    consolidated_count = sum(1 for o in clean_orders if o.ingestion_count > 1)
    preserved_duplicate_count = sum(
        1 for o in clean_orders if o.stable_id_basis == "fingerprint" and o.ingestion_count == 1
    )

    interpretation_source_by_claim_id = {}
    if claim_provenance and hasattr(claim_provenance[0], "interpretation_source"):
        interpretation_source_by_claim_id = {
            id(c): p.interpretation_source for c, p in zip(claims, claim_provenance)
        }

    results, disposition = reconcile_with_disposition(claims, clean_orders, now=as_of)

    order_disposition_counts = {d.value: 0 for d in OrderDisposition}
    for d in disposition.values():
        order_disposition_counts[d.value] += 1

    result_counts = {r.value: 0 for r in Result}
    for res in results:
        result_counts[res.result.value] += 1

    ingestion_finding_counts: dict = {}
    for f in ingestion_findings:
        ingestion_finding_counts[f.finding_type] = ingestion_finding_counts.get(f.finding_type, 0) + 1

    findings = build_findings(results, disposition, clean_orders, ingestion_findings, order_provenance_by_id,
                               interpretation_source_by_claim_id)

    summary = {
        "as_of": as_of.isoformat(),
        "raw_claims_received": raw_claims_received,
        "claims_accepted": len(claims),
        "claims_rejected": len(claim_rejections),
        "claims_outside_retrieval_window": claims_outside_window_count,
        "raw_orders_received": raw_orders_received,
        "orders_accepted": raw_orders_received - len(order_rejections),
        "orders_rejected": len(order_rejections),
        "orders_after_normalization": len(clean_orders),
        "orders_preserved_as_duplicates": preserved_duplicate_count,
        "orders_consolidated_count": consolidated_count,
        "order_disposition_counts": order_disposition_counts,
        "result_counts": result_counts,
        "ingestion_finding_counts": ingestion_finding_counts,
    }
    if retrieval_metadata is not None:
        summary["alpaca_api_retrieval"] = retrieval_metadata

    rejections_payload = {
        "claims": [asdict(r) for r in claim_rejections],
        "orders": [asdict(r) for r in order_rejections],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_report(output_dir / "reconciliation_report.json", summary, findings, rejections_payload)
    write_findings_csv(output_dir / "reconciliation_findings.csv", findings)
    write_summary_txt(output_dir / "reconciliation_summary.txt", summary, findings)

    return 0


def _parse_iso(value: str) -> datetime:
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="control_cli.py")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("reconcile", help="Reconcile SafeAgent claims against Alpaca orders")
    rec.add_argument("--claims", required=True,
                      help="Path to SafeAgent claims: .json/.jsonl/.csv (enriched export), "
                           "or .db/.sqlite/.sqlite3 (the real safeagent_orders.db)")

    order_source = rec.add_mutually_exclusive_group(required=True)
    order_source.add_argument("--orders", default=None,
                               help="Path to Alpaca orders file (.json/.csv) -- mutually exclusive with --alpaca-api")
    order_source.add_argument("--alpaca-api", action="store_true",
                               help="Retrieve orders read-only, live, from the real Alpaca Trading API "
                                    "instead of a file. Requires --after and --until. Mutually exclusive with --orders.")

    env_group = rec.add_mutually_exclusive_group()
    env_group.add_argument("--paper", action="store_true",
                            help="Use Alpaca's PAPER environment (the default -- this flag is accepted for "
                                 "explicitness but not required). --alpaca-api only.")
    env_group.add_argument("--live", action="store_true",
                            help="Use Alpaca's LIVE environment. Must be passed explicitly -- never inferred "
                                 "or silently switched. --alpaca-api only.")

    rec.add_argument("--after", default=None, help="ISO-8601 start of the retrieval window (--alpaca-api only)")
    rec.add_argument("--until", default=None, help="ISO-8601 end of the retrieval window (--alpaca-api only)")

    rec.add_argument("--as-of", required=True, help="ISO-8601 timestamp to reconcile as of")
    rec.add_argument("--output-dir", required=True, help="Directory to write the three report files into")
    rec.add_argument("--source-account", default=None,
                      help="Account identifier to attach to claims/orders that don't carry one themselves "
                           "(neither the real safeagent_orders.db schema, most Alpaca file exports, nor the "
                           "live Trading API response records it per row). Never guessed if omitted.")
    rec.add_argument("--source-system", default=None,
                      help="Strategy/bot identifier to attach to claims decoded from a .db file whose "
                           "request_id predates the STRATEGY_ID-keyed format (older real rows have no "
                           "strategy embedded at all). Ignored for .json/.jsonl/.csv claims input, and "
                           "never overwrites a source_system actually decoded from a request_id.")
    rec.add_argument("--bot-log", default=None,
                      help="Optional path to a bot's own text log, joined against .db-sourced claims to "
                           "derive caller_interpretation where the database itself doesn't record it. "
                           "Each derived value is marked observed or inferred in the report -- never "
                           "presented as something the database itself stated. Ignored for "
                           ".json/.jsonl/.csv claims input, which already carries its own interpretation.")

    args = parser.parse_args(argv)

    if args.command == "reconcile":
        try:
            as_of = _parse_iso(args.as_of)
        except ValueError:
            print(f"error: --as-of is not a valid ISO-8601 timestamp: {args.as_of!r}", file=sys.stderr)
            return 2

        if not Path(args.claims).is_file():
            print(f"error: --claims file not found: {args.claims!r}", file=sys.stderr)
            return 2
        if args.bot_log and not Path(args.bot_log).is_file():
            print(f"error: --bot-log file not found: {args.bot_log!r}", file=sys.stderr)
            return 2

        after_dt = until_dt = None
        paper = True
        if args.alpaca_api:
            if not args.after or not args.until:
                print("error: --alpaca-api requires both --after and --until", file=sys.stderr)
                return 2
            try:
                after_dt = _parse_iso(args.after)
                until_dt = _parse_iso(args.until)
            except ValueError:
                print("error: --after/--until must be valid ISO-8601 timestamps", file=sys.stderr)
                return 2
            paper = not args.live  # default True; only an explicit --live flips it -- never inferred
        else:
            if args.live or args.paper:
                print("error: --paper/--live only apply with --alpaca-api", file=sys.stderr)
                return 2
            if not Path(args.orders).is_file():
                print(f"error: --orders file not found: {args.orders!r}", file=sys.stderr)
                return 2

        try:
            return run_reconcile(
                args.claims, args.orders, as_of, Path(args.output_dir),
                source_account=args.source_account, source_system=args.source_system,
                bot_log_path=args.bot_log,
                alpaca_api=args.alpaca_api, paper=paper, after=after_dt, until=until_dt,
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        except Exception as e:
            # Covers AlpacaRetrievalError too -- its message is already
            # credential-sanitized by alpaca_api_adapter.py before it ever
            # reaches an exception object, so printing it here is safe.
            print(f"error: reconciliation failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())