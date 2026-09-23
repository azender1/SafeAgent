#!/usr/bin/env python3
"""Graham's first-failure mutation, reproduced without Stripe credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def verify(bundle: Path, flow: Path):
    process = subprocess.run(
        [sys.executable, str(HERE / 'verify_bundle.py'), str(bundle), '--flowsignal-root', str(flow)],
        text=True, capture_output=True,
    )
    if not process.stdout:
        raise AssertionError(process.stderr)
    return process.returncode, json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path, help='an unchanged simulated bundle made by run_fixture.py')
    parser.add_argument('--flowsignal-root', type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    flow = args.flowsignal_root.resolve()
    code, result = verify(bundle, flow)
    assert code == 0 and result['passed'] and result['problems'] == [], result

    with tempfile.TemporaryDirectory(prefix='ec009-first-failure-') as tmp:
        altered = Path(tmp) / 'coordinated-claim-mutation'
        shutil.copytree(bundle, altered)
        summary_path = altered / 'summary.json'
        manifest_path = altered / 'manifest.json'
        summary = json.loads(summary_path.read_text())
        summary['mode'] = 'stripe-test'
        summary['provider'] = 'STRIPE_TEST_MODE_CONFIRMED'
        summary['limitations'] = ['Actual Stripe Test Mode execution confirmed']
        summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
        manifest = json.loads(manifest_path.read_text())
        manifest['claim_scope'] = 'external Stripe Test Mode execution'
        manifest['files_sha256']['summary.json'] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        code, result = verify(altered, flow)
        assert code != 0 and not result['passed'], result
        assert any('summary.mode' in p for p in result['problems']), result
        assert any('manifest claims contradict' in p for p in result['problems']), result
        print(json.dumps({'unchanged_bundle': 'PASS', 'graham_coordinated_mutation': 'REJECTED',
                          'problems': result['problems']}, indent=2))


if __name__ == '__main__':
    main()
