# CrewAI retry: SafeAgent A/B, v3 (Crashpoint-strength isolation)

Validates that SafeAgent 0.1.23 prevents a second external effect during
CrewAI 1.15.21's real, internal `ToolUsage._use` retry -- with the effect
recorded through a genuinely out-of-process, capability-separated ledger
the worker cannot query, reset, modify, or seal.

## What changed from v2

v2 recorded the effect directly into a SQLite file the worker process
opened itself. v3 replaces that with `ledger_server.py`, a separate OS
process the parent starts fresh per trial. The worker is given ONLY a
`record_token` that authorizes exactly one HTTP endpoint, `POST /record`
-- append-only, no read-back. The parent alone holds a disjoint
`control_token` (never constructed into the worker's argv or environment
anywhere in this codebase) that authorizes `reset`/`seal`/`read`. Every
one of those boundaries is enforced by the server's own per-request
token check, not by "the worker wasn't told the URL" -- and
`test_v3.py` proves it by actually trying the record_token against every
control endpoint and confirming 403, and trying the control_token against
`/record` and confirming 403 there too.

The worker's own network access is additionally locked down at the
`socket.connect()` level to the ledger's address alone, so even an
accidentally-still-enabled telemetry client or anything CrewAI pulls in
transitively cannot reach anywhere else. `CREWAI_DISABLE_TELEMETRY` and
`OTEL_SDK_DISABLED` are also set before `crewai` is imported.

## Install and run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python parent.py --k 30 --output evidence.json
```

Full run is 180 trials (30 x 2 modes x 3 cases), takes roughly 10-13
minutes. Each trial: fresh ledger process, fresh worker process, fresh
guard database.

To run the automated test suite (20 tests -- acceptance-gate invariants,
fail-closed behavior, capability isolation, network lockdown, receipt
schema):

```bash
.venv/bin/python test_v3.py
```

## Results (this delivery)

**180/180 trials PASS.** Full per-trial receipts and the summary table
are in `evidence.json`. Methodology and the honest limitations are in
`REPORT.md`.

## Claim boundary

This validates SafeAgent 0.1.23 against CrewAI 1.15.21's same-process
synchronous `ToolUsage._use` retry, using a scripted deterministic LLM
and a harmless effect recorded through an out-of-process,
capability-separated ledger. It does not validate process death,
fresh-worker recovery, a real provider, or SafeAgent Control
reconciliation.

## Files

| File | Purpose |
|---|---|
| `ledger_server.py` | Standalone out-of-process ledger. Record-only and control capabilities are enforced by disjoint bearer tokens. |
| `worker.py` | Runs one CrewAI trial. Holds only the record_token. Network-locked to the ledger's address only. |
| `parent.py` | Sole holder of control_token. Starts/resets/seals/reads the ledger, launches the worker, validates every invariant, writes receipts. |
| `test_v3.py` | 20 automated tests: acceptance-gate invariants, fail-closed paths, duplicate/ordering rejection, capability isolation, network lockdown, receipt schema. |
| `evidence.json` | The real, executed 180-trial run delivered with this package. |
| `REPORT.md` | Methodology and results, in prose. |
| `SHA256SUMS.txt` | Hashes of every file in this delivery. |
