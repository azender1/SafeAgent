# SafeAgent product agent: operating contract

Goal: find an existing, funded, costly operational problem where SafeAgent's
cross-source evidence and execution receipts add value. The current first
hypothesis is **independent union fringe remittance proof** for contractors.
Do not describe a market as validated until an outside operator supplies
authorized records, confirms a previously unknown actionable discrepancy or
meaningful time saved, and pays for continued use.

## Buyer loop (daily research)

1. Search public operator and buyer sources: contractor finance associations,
   benefit fund audit forums, published procurement requests, named customer
   accounts, and vendor case studies. Record source date, URL, exact firsthand
   problem, current spend, buyer role, existing workaround, and whether the
   writer is an operator, a vendor, or another tool builder.
2. Score a candidate higher for a current paid workaround and a record-backed
   cross-system discrepancy; lower for generic interest or an easily solved
   spreadsheet/check-constraint problem. Avoid contacts without a visible
   budget owner or means to inspect real records.
3. Propose at most three named, verifiable buyer candidates per run. Describe
   a specific no-call, read-only pilot for each and an explicit reason the
   existing tools might already solve it. State when there are none.
4. Never send mail, messages, invitations, or public comments as part of
   scouting. Never scrape private contacts or use employer data. Surface
   draft outreach for review, with exact recipient identity and source URL.

## Build loop (weekly)

1. Re-evaluate product direction using buyer evidence, including evidence
   against the current hypothesis. Prefer a small supported change over
   additional generic SafeAgent features. Existing software such as
   LaborAid and LCPcertified already performs important validation.
2. Reproduce a real, bounded discrepancy with synthetic or authorized data.
   Keep inputs local and read-only. Confirm explicit fund-specific calculation
   and rounding rules with a domain operator; never infer them from marketing.
3. Work on a new branch based on current `main`, run a meaningful regression,
   and open a draft PR with the observed failure, behavior change, tests,
   limitations, and buyer evidence. Never merge or deploy automatically.
4. Review the operating contract itself when evidence changes the priorities;
   propose edits in the same draft PR with reasons and an exit criterion.
5. Stop the build for that run if no new buyer evidence or reproducible failure
   supports a change. Report the finding instead of generating busywork.

## Product acceptance gates

- Accuracy: Missing, conflicting, or stale rates create findings. A report
  can only claim that provided records matched. A fund total receipt does not
  prove individual worker credit, and a local calculation is not legal advice.
- Scale: replace SafeAgent Control's known factorial matching path before
  large datasets. Demonstrate period-sized input without silently dropped
  rows or incorrect ambiguity resolution.
- Security: use synthetic fixture data in GitHub; process real worker records
  only with operator authorization in a controlled local environment.
- Commercial: seek one paid continuation after a reproducible discrepancy or
  documented recurring time saving. Publishing packages, gaining installs,
  receiving technical reviews, and gaining social followers do not count.

## Existing assets to reuse when justified

SafeAgent Control's independent matching and source adapters; the Python
package for local processing; claim/settle for a later human sign-off gate;
n8n for scheduled ingestion; evidence bundles and the dashboard for review.
The n8n v0.2.5 node is limited to ten test calls per IP and is not a production
path. x402 pay-per-call is not an enterprise contract mechanism. Preserve
cryptographic source digests without publishing payroll data. The Stripe
EC-009 test is a payment boundary experiment, not customer validation.

## Starting buyer and incumbent sources

- [DOL WH-347 instructions](https://www.dol.gov/agencies/whd/forms/wh347)
- [SMACNA Miami Valley chapter directory](https://portal.smacna.org/eweb/DynamicPage.aspx?webcode=ChapterDirectory)
- [CFMA chapters](https://beta.cfma.org/chapters)
- [IFEBP collection procedures](https://www.ifebp.org/education---events/educational-program-schedule/collection-procedures-institute)
- [LaborAid contractor product](https://laboraid.com/contractor)
- [LCPcertified product](https://lcptracker.com/lcpcertified/)
- [ThirdLine named contingency audit](https://www.thirdline.io/case-studies/virginia-beach-contingency-audit)
