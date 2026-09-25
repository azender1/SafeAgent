# SafeAgent Control: Fringe Proof (pilot slice)

A local, read-only check of **provided** payroll hours, effective-dated fund
rates, worker-level remittance lines, and aggregate fund receipts. It never
initiates payroll or payment. No employer or worker data is sent to SafeAgent.

The included synthetic example demonstrates a $8.00 discrepancy: a fund
acknowledges exactly the submitted $160.00, while the approved rate schedule
and covered hours imply $168.00 for that worker after a mid-period rate change.
An acknowledgment for an aggregate fund amount cannot establish worker-level
credit or a legally compliant contribution.

Run with Python 3.10+ and no third-party dependencies:

```bash
cd products/fringe_proof
python reconcile.py \
  --payroll fixtures/payroll.csv --rates fixtures/rates.csv \
  --remittance fixtures/remittance.csv --fund-ack fixtures/fund_ack.csv \
  --period-start 2026-09-01 --period-end 2026-09-07 \
  --output /tmp/fringe-proof-report.json
```

Exit code 2 means the report contains findings. Exit code 0 means the
**provided records** matched within this narrow comparison; it does not
certify wage compliance, fund credit, or completeness of the input exports.
Input SHA-256 digests and a canonical report digest are saved in the JSON.
Keep real input files and reports in an access-controlled local location;
never commit them to GitHub.

## Required columns

| CSV | Columns |
| --- | --- |
| payroll | `employee_id,work_date,local,classification,covered_hours` |
| rates | `local,classification,fund,effective_from,effective_to,rate_per_hour` |
| remittance | `employee_id,local,classification,fund,reported_hours,reported_amount` |
| fund_ack | `local,fund,amount_received` |

Rates must be supplied and approved by the operator. `effective_to` may be
blank. One unambiguous rate for each fund and work date is required. Remittance
amounts/hours may be negative for corrections; this pilot aggregates them by
worker, local, classification and fund. Fund receipts are compared by local
and fund for this period; if the fund issues separate batches, aggregate them
under the operator's documented process before using this CSV. The pilot uses
half-up rounding at the worker/fund period total. An operator must confirm
the applicable fund agreement uses that rule before interpreting amounts.

The product question is whether independent cross-source checks catch a
previously unknown, actionable discrepancy, or remove meaningful manual
review time. Current competitors already validate payroll reports and automate
remittances. This code is a test of a narrower gap, not market validation.

## Next engineering gate

SafeAgent Control's existing matching prototype has a reported factorial
slowdown above nine claims. Replace that matching path and establish a
representative large-period benchmark before adapting it for a real employer.
Do not add automatic certification or fund payments without a verified source
of rules and an explicit human review workflow. The existing SafeAgent
claim/settle layer can then gate a sign-off; n8n, Python, MCP, dashboard,
and evidence receipts can import, display, or preserve the reviewed result.
