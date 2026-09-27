#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, urllib.error, urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/"COMPANY_STATE.json"
BASE=os.getenv("SAFEAGENT_BASE_URL","https://safeagent-production.up.railway.app").rstrip("/")
GH_REPO=os.getenv("GITHUB_REPOSITORY","azender1/SafeAgent")
GH_TOKEN=os.getenv("GITHUB_TOKEN","")

def get_json(url, headers=None):
    req=urllib.request.Request(url,headers=headers or {})
    with urllib.request.urlopen(req,timeout=20) as r:
        return r.status,json.loads(r.read().decode())

def get_status(url, headers=None):
    req=urllib.request.Request(url,headers=headers or {})
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

def repo_versions():
    pkg=json.loads((ROOT/"n8n-nodes-safeagent/package.json").read_text())
    py=(ROOT/"pyproject.toml").read_text()
    m=re.search(r'^version\s*=\s*"([^"]+)"',py,re.M)
    return pkg["version"],m.group(1)

def latest_registry_versions():
    _,npm=get_json("https://registry.npmjs.org/n8n-nodes-safeagent/latest")
    _,pypi=get_json("https://pypi.org/pypi/safeagent-exec-guard/json")
    return npm["version"],pypi["info"]["version"]

def github_summary():
    headers={"Accept":"application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"]=f"Bearer {GH_TOKEN}"
    _,prs=get_json(f"https://api.github.com/repos/{GH_REPO}/pulls?state=open&per_page=100",headers)
    _,runs=get_json(f"https://api.github.com/repos/{GH_REPO}/actions/runs?branch=main&per_page=30",headers)
    relevant=[r for r in runs.get("workflow_runs",[]) if r.get("name") in {"ci","control-v15","n8n-node-build"}]
    latest={}
    for r in relevant:
        latest.setdefault(r["name"],{"status":r["status"],"conclusion":r.get("conclusion"),"url":r["html_url"]})
    green=all(v["status"]=="completed" and v["conclusion"]=="success" for v in latest.values()) if latest else False
    return len(prs),green,latest

def main():
    old={}
    if STATE.exists():
        try: old=json.loads(STATE.read_text())
        except Exception: old={}
    health=get_status(BASE+"/health")
    audit=get_status(BASE+"/audit")
    repo_npm,repo_pypi=repo_versions()
    npm,pypi=latest_registry_versions()
    open_prs,ci_green,ci=github_summary()
    manual=old.get("commercial",{})
    active_evaluator=manual.get("active_evaluator",False)
    pilot=manual.get("pilot",False)
    revenue=manual.get("revenue",0)
    drifts=[]
    if repo_npm!=npm: drifts.append(f"npm repo={repo_npm} registry={npm}")
    if repo_pypi!=pypi: drifts.append(f"pypi repo={repo_pypi} registry={pypi}")
    if health!=200 or audit!=403: drifts.append(f"production health={health} unauth_audit={audit}")
    if not ci_green: drifts.append("main CI not fully green")
    bottleneck=drifts[0] if drifts else ("obtain first active evaluator" if not active_evaluator else "advance evaluator to pilot")
    state={
      "updated_at":datetime.now(timezone.utc).isoformat(),
      "production":{
        "health_http":health,
        "unauthenticated_audit_http":audit,
        "status":"PASS" if health==200 and audit==403 else "FAIL"
      },
      "releases":{
        "n8n":{"repo":repo_npm,"registry":npm,"match":repo_npm==npm},
        "pypi":{"repo":repo_pypi,"registry":pypi,"match":repo_pypi==pypi}
      },
      "github":{"open_pull_requests":open_prs,"main_ci_green":ci_green,"latest_checks":ci},
      "external_review":{"status":old.get("external_review",{}).get("status","awaiting Graham final EC-009 wording")},
      "commercial":{"active_evaluator":active_evaluator,"pilot":pilot,"revenue":revenue},
      "next_bottleneck":bottleneck,
      "drift":drifts
    }
    STATE.write_text(json.dumps(state,indent=2,sort_keys=True)+"\n")
    print(json.dumps(state,indent=2))
    if health!=200 or audit!=403:
        raise SystemExit("production security/health check failed")

if __name__=="__main__":
    main()
