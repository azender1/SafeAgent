#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    root = args.bundle.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for rel, expected in manifest.get("files_sha256", {}).items():
        path = root / rel
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            problems.append({"path": rel, "expected": expected, "actual": actual})
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    replay = summary.get("safeagent_replay")
    if replay != {"decision": "BLOCKED", "reason": "permit_already_consumed"}:
        problems.append({"summary": "replay was not blocked as expected", "actual": replay})
    print(json.dumps({"mode": manifest.get("mode"), "files": len(manifest.get("files_sha256", {})), "problems": problems}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

