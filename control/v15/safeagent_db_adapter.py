"""
safeagent_db_adapter.py

Reads the REAL safeagent_orders.db schema directly -- not an enriched
export. Confirmed against the actual production schema:

    CREATE TABLE orders (
        request_id TEXT PRIMARY KEY,
        result     TEXT,
        status     TEXT DEFAULT 'PENDING',
        created_at TEXT DEFAULT (datetime('now'))
    )

    result = json.dumps(str(broker_response_dict))   # see _parse_result_column()

REQUEST_ID FORMAT: confirmed by inspecting the actual production database
(a currently-running bot's source code alone is not sufficient evidence
here -- the format has evolved, and old committed rows are never
rewritten, so a real database contains a MIX of historical shapes). Three
`order:`-prefixed shapes were found in the real data, disambiguated by
locating the ISO-timestamp substring itself rather than by naively
splitting on every colon (the timestamp contains colons of its own, e.g.
"09:33:00-04:00", and free-form exit-reason suffixes have been observed
containing spaces and "=" but not, so far, colons):

  4 fields before the timestamp -- "order:{symbol}:{side}:{qty}:{bar_ts}",
      optionally followed by ":{free-form exit-reason suffix}" (older
      rows; no strategy segment at all)
        e.g. order:SQQQ:sell:8:2026-05-26T09:42:00-04:00:LOSS_STOP_BEAR

  5 fields before the timestamp -- "order:{strategy}:{symbol}:{side}:{qty}:{bar_ts}"
      (current STRATEGY_ID-keyed format, no stage)
        e.g. order:SAMPLE_STRATEGY:SQQQ:buy:12:2026-09-01T13:10:00-04:00

  6 fields before the timestamp -- "order:{strategy}:{symbol}:{side}:{qty}:{stage}:{bar_ts}"
      (current format, with stage)
        e.g. order:SAMPLE_STRATEGY:SQQQ:buy:12:add1:2026-09-08T09:35:00-04:00

A separate `exit:{symbol}:{qty}:{timestamp}` family also exists in the
real data (e.g. exit:SQQQ:12:2026-05-26T09:36:39.734952-04:00) -- it has
NO side field recorded anywhere in the key, and side cannot be inferred
from symbol/qty/timestamp alone without guessing. Per this project's
"never invent a required field" rule, rows in this family are explicitly
rejected with that reason rather than assigned a guessed side.

SCOPE, STATED PLAINLY: this adapter reads the `orders` table only. The
same database also has `exit_claims` (symbol/qty/entry_ts/status columns
already decomposed, but no `side` column at all -- same problem as the
`exit:`-keyed rows above) and `phantom_positions` (a symbol-quarantine
list, not claim data at all). Neither is read here, for the same reason.
See BUILD_STATUS.md.

WHY caller_interpretation IS NEVER REQUIRED HERE (unlike
safeagent_claims_parser.py's file-based path): the raw `orders` table
records only the FINAL state of a deduplicated request_id -- a repeat
attempt that hits the dedup guard never creates its own row; it just
returns the existing row's cached result. Whether the CALLER treated that
return value as a fresh success or recognized it as a duplicate is
therefore not recoverable from this table alone, for any row. Rejecting
every row for missing caller_interpretation would mean rejecting the
entire production database. Per the correction request: default to
Interpretation.UNKNOWN, and only assign something more specific when a
join against the bot's own text logs actually supports it (see
join_bot_log_interpretations() below) -- and even then, mark plainly
whether that interpretation was OBSERVED (a log line directly states it)
or INFERRED (derived from what's present/absent), never presenting a
guess as a fact the database itself recorded.
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from reconcile_v12 import InternalClaim, Interpretation, FailureType


# Finds the ISO-timestamp substring ANYWHERE in the request_id, rather
# than assuming its position -- this is what makes one decoder handle all
# three real `order:`-prefixed shapes without trying each as a guess.
# Accepts 0-6 digits of fractional seconds (real rows have been seen with
# full microsecond precision, e.g. ".734952").
_TS_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?[+-]\d{2}:\d{2}')
_VALID_SIDES = {"buy", "sell"}


@dataclass
class RejectedRecord:
    record_number: int  # 1-based row order as read from the DB (no natural row number exists; assigned on read)
    reason: str
    raw: dict


@dataclass
class ClaimProvenance:
    source_file: str
    source_format: str  # 'safeagent_db'
    record_number: int
    raw: dict
    interpretation_source: str  # 'default_unknown' | 'log_observed' | 'log_inferred'


def decode_request_id(request_id: str) -> Optional[dict]:
    """
    Best-effort decode against all three real `order:`-prefixed shapes
    documented in the module docstring. Returns None if the string
    doesn't start with "order:" or no timestamp substring can be located
    in it at all -- callers must reject the row in that case rather than
    guess a symbol/side/qty from nothing. `exit:`-prefixed rows are
    handled by the caller (parse_safeagent_db), not here, since their
    rejection reason ("no side field") is more specific than a generic
    "unrecognized format".
    """
    if not request_id.startswith("order:"):
        return None

    m = _TS_RE.search(request_id)
    if not m:
        return None
    bar_ts_str = m.group(0)
    prefix = request_id[:m.start()].rstrip(":")
    suffix = request_id[m.end():]
    if suffix.startswith(":"):
        suffix = suffix[1:]
    suffix = suffix or None

    fields = prefix.split(":")  # fields[0] == "order" always, by the startswith check above
    if len(fields) == 4:            # order:symbol:side:qty  (oldest shape; suffix, if any, is the stage/exit-reason)
        _, symbol, side, qty_str = fields
        strategy, stage = None, suffix
    elif len(fields) == 5:          # order:strategy:symbol:side:qty  (current shape, no stage)
        _, strategy, symbol, side, qty_str = fields
        stage = suffix               # normally None; tolerated if a stray suffix exists
    elif len(fields) == 6:           # order:strategy:symbol:side:qty:stage  (current shape, with stage)
        _, strategy, symbol, side, qty_str, stage = fields
    else:
        return None

    try:
        qty = float(qty_str)
    except ValueError:
        return None
    if qty <= 0:
        return None

    try:
        bar_ts = datetime.fromisoformat(bar_ts_str)
    except ValueError:
        bar_ts = None

    return {"strategy": strategy, "symbol": symbol, "side": side.lower(), "qty": qty,
            "stage": stage, "bar_ts": bar_ts, "bar_ts_raw": bar_ts_str}


def _parse_result_column(raw_result: Optional[str]) -> dict:
    """
    The `result` column is written as `json.dumps(str(broker_response))`
    -- i.e. the broker's response dict is first stringified with Python's
    own repr (single-quoted, not valid JSON), and THAT string is then
    JSON-encoded as a single string literal. So recovering the original
    dict takes two steps: json.loads() to unwrap the outer JSON-string
    layer, then ast.literal_eval() (NOT json.loads again -- the inner
    content is a Python repr, not JSON) to get the actual dict back.

    Defensively also accepts a row where some other write path stored
    clean JSON directly. Returns {} (never raises) if the column is
    NULL/empty or genuinely unparseable by any of these paths -- a
    pending, never-committed claim has no result yet, and that is a
    normal, expected state, not an error.
    """
    if not raw_result:
        return {}
    try:
        unwrapped = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        unwrapped = raw_result

    candidate = unwrapped
    if isinstance(candidate, str):
        try:
            candidate = ast.literal_eval(candidate)
        except (ValueError, SyntaxError):
            try:
                candidate = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                return {}

    if not isinstance(candidate, dict):
        return {}
    return {
        "broker_order_id": candidate.get("id"),
        "client_order_id": candidate.get("client_order_id"),
        "filled_avg_price": candidate.get("filled_avg_price"),
    }


# ----------------------------- bot-log join -----------------------------
# Scoped deliberately narrow, and matched against ACTUAL log output (not
# assumed from source code): a real production log shows SAFEAGENT SKIP
# lines with NO stage field at all --
#   "SAFEAGENT SKIP: duplicate order blocked buy TQQQ qty=7 bar=... -- returning cached result"
# -- while ENTER lines do carry one --
#   "ENTER BULL stage=base symbol=TQQQ qty=8 ... bar=2026-07-29T12:52:00-04:00 ..."
# Both real line types DO carry the bar= trading-bar timestamp, which is
# the most reliable join key available in both request_id-decoded claims
# and log text alike (unlike "stage", which for the OLDER
# non-STRATEGY_ID-keyed request_id shapes is actually a free-form
# exit-reason suffix, not a short token comparable to a log's stage=
# field at all). Matching on (symbol, qty, bar_ts) -- and side, where the
# line provides it -- works uniformly across every real request_id shape
# this module decodes.
_SKIP_LINE_RE = re.compile(
    r'SAFEAGENT SKIP: duplicate order blocked (?P<side>\w+) (?P<symbol>\S+) qty=(?P<qty>[0-9.]+) '
    r'(?:stage=\S+ )?bar=(?P<bar_ts>\S+)'
)
_ENTER_LINE_RE = re.compile(
    r'ENTER \w+ stage=\S+ symbol=(?P<symbol>\S+) qty=(?P<qty>[0-9.]+).*? bar=(?P<bar_ts>\S+)'
)


def join_bot_log_interpretations(claims: list[InternalClaim], decoded: list[dict], log_path: str) -> list[tuple]:
    """
    Scans a bot's own text log for the two line grammars above and
    returns a list parallel to `claims` of (Interpretation, source)
    tuples, source in {'log_observed', 'log_inferred', 'default_unknown'}.
    Does not mutate the claims (InternalClaim is a frozen v12.1 core
    dataclass) -- the CLI applies this by constructing NEW InternalClaim
    objects with the derived interpretation where evidence was found.

    Matches on (symbol, qty, bar_ts_raw) -- the trading-bar timestamp
    string exactly as it appears in the request_id, which both real line
    grammars also carry. Side is checked too when matching a SKIP line,
    since that grammar provides it.

    'log_observed': a SAFEAGENT SKIP line for this (side, symbol, qty,
        bar_ts) was found -- the guard's own log directly states the
        outcome.
    'log_inferred': no SKIP line was found, but an ENTER line for this
        (symbol, qty, bar_ts) was -- the caller's log shows it proceeding
        as if successful. Inferred from what's present, not a direct
        statement of "the caller believed this succeeded".
    'default_unknown': neither line was found, OR the claim's bar_ts
        couldn't be decoded at all (nothing to match on).
    """
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    skips = set()
    for m in _SKIP_LINE_RE.finditer(text):
        skips.add((m.group("side").lower(), m.group("symbol"), float(m.group("qty")), m.group("bar_ts")))

    enters = set()
    for m in _ENTER_LINE_RE.finditer(text):
        enters.add((m.group("symbol"), float(m.group("qty")), m.group("bar_ts")))

    results = []
    for d in decoded:
        if d is None or not d.get("bar_ts_raw"):
            results.append((Interpretation.UNKNOWN, "default_unknown"))
            continue
        key_skip = (d["side"], d["symbol"], d["qty"], d["bar_ts_raw"])
        key_enter = (d["symbol"], d["qty"], d["bar_ts_raw"])
        if key_skip in skips:
            results.append((Interpretation.SKIPPED, "log_observed"))
        elif key_enter in enters:
            results.append((Interpretation.SUCCESS, "log_inferred"))
        else:
            results.append((Interpretation.UNKNOWN, "default_unknown"))
    return results


# ----------------------------- main entry point -----------------------------

def parse_safeagent_db(
    db_path: str, source_account: Optional[str] = None, bot_log_path: Optional[str] = None,
) -> tuple[list[InternalClaim], list[RejectedRecord], list[ClaimProvenance]]:
    """
    Reads the real `orders` table and produces InternalClaim objects.

    - status is passed straight through as `outcome` (the table's real
      values are 'PENDING'/'COMMITTED', which already match what
      normalize_inputs() expects -- no translation needed or performed).
    - symbol/side/qty/stage are decoded from request_id; a request_id
      that doesn't match either known shape rejects the row (never
      guessed).
    - broker_order_id/client_order_id come from the `result` column when
      present and parseable; otherwise left None (a pending claim has
      none yet -- that's normal, not an error).
    - caller_interpretation defaults to UNKNOWN. If bot_log_path is
      given, it's joined per join_bot_log_interpretations() and the
      result (with its observed/inferred provenance) is used instead.
    - source_account is not present in this table at all; it comes
      entirely from the `source_account` parameter (wired to the CLI's
      `--source-account` flag). Left None if not given -- never guessed.
    """
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute("SELECT request_id, result, status, created_at FROM orders ORDER BY created_at, request_id")
        rows = cur.fetchall()
    finally:
        con.close()

    claims: list[InternalClaim] = []
    rejections: list[RejectedRecord] = []
    provenance: list[ClaimProvenance] = []
    decoded_for_log_join: list[Optional[dict]] = []
    raw_rows: list[dict] = []

    for record_number, (request_id, result_raw, status, created_at) in enumerate(rows, start=1):
        raw = {"request_id": request_id, "result": result_raw, "status": status, "created_at": created_at}

        if request_id and request_id.startswith("exit:"):
            rejections.append(RejectedRecord(
                record_number,
                "request_id belongs to the 'exit:{symbol}:{qty}:{timestamp}' key family, which "
                "records no side field anywhere -- cannot construct a valid claim without "
                "inventing one",
                raw,
            ))
            continue

        decoded = decode_request_id(request_id) if request_id else None
        if decoded is None:
            rejections.append(RejectedRecord(
                record_number,
                f"request_id does not match either known SafeAgent dedup-key format: {request_id!r}",
                raw,
            ))
            continue

        result_fields = _parse_result_column(result_raw)

        claimed_at = decoded["bar_ts"]
        if claimed_at is None:
            try:
                claimed_at = datetime.fromisoformat(created_at) if created_at else None
            except (ValueError, TypeError):
                claimed_at = None
        if claimed_at is None:
            rejections.append(RejectedRecord(
                record_number,
                f"could not determine a claim timestamp: bar_ts undecodable from request_id "
                f"({decoded['bar_ts_raw']!r}) and created_at unusable ({created_at!r})",
                raw,
            ))
            continue

        if decoded["side"] not in _VALID_SIDES:
            rejections.append(RejectedRecord(
                record_number, f"decoded side is not 'buy'/'sell': {decoded['side']!r}", raw
            ))
            continue

        raw_rows.append(raw)
        decoded_for_log_join.append(decoded)
        claims.append(InternalClaim(
            request_id=request_id, symbol=decoded["symbol"], side=decoded["side"], qty=decoded["qty"],
            stage=decoded["stage"] or "unstaged", claimed_at=claimed_at,
            outcome=status or "PENDING", caller_interpretation=Interpretation.UNKNOWN,
            broker_order_id=result_fields.get("broker_order_id"),
            client_order_id=result_fields.get("client_order_id"),
            failure_type=FailureType.NONE,
            source_system=decoded["strategy"], source_account=source_account,
        ))
        provenance.append(ClaimProvenance(
            source_file=db_path, source_format="safeagent_db", record_number=record_number,
            raw=raw, interpretation_source="default_unknown",
        ))

    if bot_log_path:
        joined = join_bot_log_interpretations(claims, decoded_for_log_join, bot_log_path)
        rebuilt_claims = []
        for claim, (interp, source) in zip(claims, joined):
            if interp is not Interpretation.UNKNOWN:
                claim = InternalClaim(
                    request_id=claim.request_id, symbol=claim.symbol, side=claim.side, qty=claim.qty,
                    stage=claim.stage, claimed_at=claim.claimed_at, outcome=claim.outcome,
                    caller_interpretation=interp,
                    broker_order_id=claim.broker_order_id, client_order_id=claim.client_order_id,
                    failure_type=claim.failure_type,
                    source_system=claim.source_system, source_account=claim.source_account,
                )
            rebuilt_claims.append(claim)
        claims = rebuilt_claims
        for p, (interp, source) in zip(provenance, joined):
            p.interpretation_source = source

    return claims, rejections, provenance