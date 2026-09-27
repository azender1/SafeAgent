# SafeAgent Control v15 — public review package

SafeAgent Control is a read-only reconciliation CLI for comparing SafeAgent claim/audit records with external order records. This public package is a sanitized publication boundary: private real-account acceptance evidence is not part of it.

## Included public evidence

- v15 source/test provenance and package hash;
- synthetic fixtures only;
- the authorized five-line synthetic trading log;
- a 45-row synthetic database export for public inspection;
- a public status statement that separates technical evidence from commercial demand.

## Privacy boundary

Do not publish private Alpaca acceptance reports, raw real-account findings, credentials, account identifiers, or locally retained evidence. The private acceptance exercise is not public proof and is not reproduced here.

## Security boundary

SafeAgent Control is read-only reconciliation. It does not submit, cancel, replace, or close broker orders. Hosted SafeAgent API authentication/tenant isolation is tracked separately in PR #19.

## Package provenance

Sanitized v15 public package SHA-256:

`5cc871b69c5e24f944392bf8999f4615adf1fd44f5910105ceb8494b25f137df`

The original local v15 source was audited before preparing the public boundary. The public package excludes private acceptance reports and evidence outputs.

## Verification status

The standalone v15 source previously recorded a passing test baseline. A fresh run in the current ChatGPT execution environment could not collect the full suite because `alpaca-py` and `hypothesis` are not installed there. The hosted API hardening branch now has green GitHub Actions CI after its Stripe webhook compatibility test was fixed.

This publication does not claim production deployment, a paying customer, or commercial validation.


## Fresh clone verification

From `control/v15`:

```bash
pip install -r requirements.txt
pytest -q
```

The test session builds the 45-row synthetic SQLite fixture locally from the committed CSV, so a fresh clone does not depend on GitHub Actions or any private database.
