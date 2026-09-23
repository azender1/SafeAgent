# Graham's first independent hostile claim test

Graham's review on issue #4, comment `5799346245`, reported a false positive. He
changed the simulated `summary.json` into a claim of external Stripe Test Mode
execution, changed `manifest.claim_scope` to match, and recalculated the summary
checksum. The original verifier returned `passed: true` even though the primary
provider record remained `source: SIMULATED` and no Stripe call occurred.
The original verifier at `94a80471e3b0a30e22ee570ceb1ec03aa477e8dd`
was rerun against the paired edit: `passed: true`, `problems: []`.

`test_claim_integrity.py` retains that coordinated mutation as an executable
regression. The verifier now checks every summary field against primary records
and requires the manifest mode and claim scope to agree with those records. A
checksum on an attacker-edited manifest proves only internal byte consistency;
it does not establish that its claims are true. This bundle remains simulation
evidence only.
