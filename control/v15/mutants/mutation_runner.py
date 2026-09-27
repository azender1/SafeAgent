"""
Mutation harness (v12, item 1).

THE ONE COMMAND, from a freshly extracted package, from any directory:

    python3 mutants/mutation_runner.py

There is no `cd` step and no second variant of this command anywhere in the
package. The script resolves its own location, so the working directory is
irrelevant. v11's report, runner docstring, and actual file layout each
named a different directory, and none of them worked.

WHAT IT RUNS
------------
The deterministic + fixture + ingestion tests, DISCOVERED FROM THE SUITE AT
RUNTIME rather than from a hardcoded list. v11 used a hand-maintained list
of test names, 26 of which had gone missing from it -- so four mutants
appeared to survive when the tests that would have killed them simply were
not being run. A list that can drift out of sync with the suite is a
reporting hazard, so there is no list any more: if a test exists in the
suite and takes no Hypothesis arguments, it runs here.

Hypothesis property tests are EXCLUDED from the mutation baseline for
runtime reasons. That is a real limitation, stated plainly, not hidden:
they are run by `python3 test_v12_full_suite.py`, which reports
deterministic and property results separately.

SCOPE, stated accurately: eleven hand-written mutants targeting eleven
named behaviors is a TARGETED mutation suite. It is not a mutation score.
A real score would require systematically mutating every operator and
branch with a tool such as mutmut or cosmic-ray, which has not been done.
"""
from __future__ import annotations

import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(HERE)
GENERATED = os.path.join(HERE, "generated")

sys.path.insert(0, PACKAGE_ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, GENERATED)

import generate_mutants  # noqa: E402  (path set up above)


def discover_deterministic_tests(suite):
    import inspect
    tests = []
    for name, fn in vars(suite).items():
        if not name.startswith("test_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != suite.__name__:
            continue
        if getattr(fn, "is_hypothesis_test", False) or inspect.signature(fn).parameters:
            continue  # property test -- excluded from the mutation baseline
        tests.append((name, fn))
    return sorted(tests)


def run_suite(suite, patches: dict) -> dict:
    """Applies the given module-level patches to the suite and runs every
    deterministic test, capturing which ones fail and why."""
    original = {k: getattr(suite, k) for k in patches}
    for k, v in patches.items():
        setattr(suite, k, v)
    try:
        results = {}
        for name, fn in discover_deterministic_tests(suite):
            try:
                fn()
                results[name] = {"status": "pass"}
            except AssertionError as e:
                results[name] = {"status": "fail", "reason": str(e)[:300]}
            except Exception as e:
                results[name] = {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:300]}"}
        return results
    finally:
        for k, v in original.items():
            setattr(suite, k, v)


def failures_of(results: dict) -> dict:
    return {k: v for k, v in results.items() if v["status"] != "pass"}


def _fresh_import(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def main() -> int:
    print(f"Generating mutants from delivered source ({PACKAGE_ROOT}) ...")
    numbers = generate_mutants.generate()
    # The generated/ directory is created after sys.path was set up, so
    # Python's cached directory listing for it must be invalidated or the
    # fresh modules are invisible.
    importlib.invalidate_caches()
    print(f"  {len(numbers)} mutants generated, every anchor matched exactly once.\n")

    # Quiet the suite's own per-test prints during the mutation sweep.
    import io
    import contextlib

    suite = importlib.import_module("test_v12_full_suite")
    total_discovered = len(discover_deterministic_tests(suite))

    report = {
        "scope": "Targeted mutation suite: 11 hand-written mutants of 11 named behaviors. "
                 "NOT a mutation score -- no systematic operator/branch mutation was performed.",
        "test_selection": "Deterministic + fixture + ingestion tests, discovered from the suite "
                          "at runtime (no hardcoded list). Hypothesis property tests are excluded "
                          "from the mutation baseline for runtime reasons and are run separately "
                          "by test_v12_full_suite.py.",
        "deterministic_tests_run_per_mutant": total_discovered,
        "baseline": None,
        "mutants": {},
    }

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        baseline = run_suite(suite, {})
    baseline_failures = failures_of(baseline)
    report["baseline"] = {"total_tests": len(baseline), "failures": len(baseline_failures),
                          "detail": baseline}
    print(f"=== BASELINE (unmutated v12, {len(baseline)} deterministic tests) ===")
    print(f"  {len(baseline) - len(baseline_failures)}/{len(baseline)} passed "
          f"{'-- clean, as required' if not baseline_failures else '-- UNEXPECTED FAILURES'}")
    if baseline_failures:
        for k, v in baseline_failures.items():
            print(f"    {k}: {v['reason']}")
    print()

    for n in numbers:
        spec = generate_mutants.MUTATIONS[n]
        if spec["target"] == "engine":
            _fresh_import(f"reconcile_v12_mut{n}")
            mod = _fresh_import(f"reconcile_v12_classify_mut{n}")
            patches = {"reconcile": mod.reconcile,
                       "reconcile_with_disposition": mod.reconcile_with_disposition,
                       "summarize_incidents": mod.summarize_incidents}
        else:
            mod = _fresh_import(f"ingestion_normalization_mut{n}")
            patches = {"normalize_ingested_orders": mod.normalize_ingested_orders}

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            results = run_suite(suite, patches)
        failed = failures_of(results)
        verdict = "KILLED" if failed else "SURVIVED"
        report["mutants"][n] = {
            "description": spec["description"], "target": spec["target"], "verdict": verdict,
            "killed_by": sorted(failed), "total_tests": len(results), "failures": len(failed),
            "detail": results,
        }
        print(f"=== MUTANT {n}: {spec['description']} ===")
        print(f"  Verdict: {verdict}")
        if failed:
            shown = sorted(failed)[:3]
            print(f"  Killed by {len(failed)} test(s), e.g. {shown}")
            print(f"    reason: {failed[shown[0]]['reason'][:160]}")
        print()

    survived = [n for n in numbers if report["mutants"][n]["verdict"] == "SURVIVED"]
    print("=== SUMMARY ===")
    print(f"  {len(numbers) - len(survived)}/{len(numbers)} mutants killed")
    print(f"  {total_discovered} deterministic tests run against each")
    if survived:
        print(f"  SURVIVED: {survived}")

    out = os.path.join(HERE, "mutation_results.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nMachine-readable output: {out}")

    return 0 if (not survived and not baseline_failures) else 1


if __name__ == "__main__":
    sys.exit(main())