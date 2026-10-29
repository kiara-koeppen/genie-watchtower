"""Run a SQL statement on a Databricks SQL warehouse via the Statement Execution API.

Usage: python scripts/sql.py "<sql>" [--profile P] [--warehouse W] [--json]
Defaults come from WATCHTOWER_PROFILE / WATCHTOWER_WAREHOUSE env vars.
"""
import argparse
import json
import os
import subprocess
import sys
import time


def run_sql(sql, profile=None, warehouse=None, timeout_s=600):
    profile = profile or os.environ.get("WATCHTOWER_PROFILE", "e2-demo-field-eng")
    warehouse = warehouse or os.environ.get("WATCHTOWER_WAREHOUSE", "fa5f84c41a5dfaeb")
    body = {"warehouse_id": warehouse, "statement": sql, "wait_timeout": "50s",
            "disposition": "INLINE", "format": "JSON_ARRAY"}
    resp = _api("post", "/api/2.0/sql/statements", profile, body)
    deadline = time.time() + timeout_s
    while resp["status"]["state"] in ("PENDING", "RUNNING"):
        if time.time() > deadline:
            raise TimeoutError(f"statement {resp['statement_id']} still running")
        time.sleep(3)
        resp = _api("get", f"/api/2.0/sql/statements/{resp['statement_id']}", profile)
    if resp["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(resp["status"].get("error", {}).get("message", resp["status"]))
    cols = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
    rows = resp.get("result", {}).get("data_array", []) or []
    return cols, rows


def _api(method, path, profile, body=None):
    cmd = ["databricks", "api", method, path, "--profile", profile]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("sql")
    p.add_argument("--profile")
    p.add_argument("--warehouse")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    cols, rows = run_sql(a.sql, a.profile, a.warehouse)
    if a.json:
        print(json.dumps([dict(zip(cols, r)) for r in rows], indent=1))
    else:
        print(" | ".join(cols))
        for r in rows:
            print(" | ".join("" if v is None else str(v) for v in r))
