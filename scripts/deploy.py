"""Dev helper: create or update the dashboard directly (outside the bundle) and publish it,
preselecting the busiest workspace and agent. Customers should use `databricks bundle deploy`.

    python scripts/deploy.py --profile e2-demo-field-eng --warehouse e3432962461d4dd0 \
        --file dashboard_materialized.json --name "Genie Watchtower"

The dashboard id per profile is remembered in .deploy_state.json so reruns update in place.
"""
import argparse
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts.sql import run_sql  # noqa: E402
from watchtower.build import build_dashboard, single_select_default  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / ".deploy_state.json"


def api(method, path, profile, body=None):
    cmd = ["databricks", "api", method, path, "--profile", profile]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return json.loads(out.stdout) if out.stdout.strip() else {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("--warehouse", required=True)
    p.add_argument("--target", default="main.genie_watchtower", help="catalog.schema of wt_* tables")
    p.add_argument("--name", default="Genie Watchtower")
    p.add_argument("--parent", help="workspace folder; defaults to the caller's home folder")
    a = p.parse_args()

    # Preselect the busiest workspace and agent so the drill-down pages open populated.
    defaults = {}
    if True:
        for kind, table, col in [("workspace", "wt_workspace_kpis", "workspace_name"),
                                 ("agent", "wt_agent_kpis", "agent_label")]:
            _, rows = run_sql(f"SELECT {col} FROM {a.target}.{table} ORDER BY questions DESC LIMIT 1",
                              a.profile, a.warehouse)
            if rows:
                defaults[kind] = single_select_default(rows[0][0])
    dash_json = build_dashboard(a.target, defaults.get("workspace"), defaults.get("agent"))
    serialized = json.dumps(dash_json)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    key = f"{a.profile}:materialized:{a.target}"
    me = api("get", "/api/2.0/preview/scim/v2/Me", a.profile)["userName"]
    parent = a.parent or f"/Workspace/Users/{me}"

    body = {"display_name": a.name, "warehouse_id": a.warehouse, "serialized_dashboard": serialized}
    if key in state:
        dash = api("patch", f"/api/2.0/lakeview/dashboards/{state[key]}", a.profile, body)
    else:
        dash = api("post", "/api/2.0/lakeview/dashboards", a.profile, {**body, "parent_path": parent})
        state[key] = dash["dashboard_id"]
        STATE.write_text(json.dumps(state, indent=2) + "\n")

    api("post", f"/api/2.0/lakeview/dashboards/{dash['dashboard_id']}/published", a.profile,
        {"warehouse_id": a.warehouse, "embed_credentials": False})
    host = subprocess.run(["databricks", "auth", "describe", "--profile", a.profile, "-o", "json"],
                          capture_output=True, text=True)
    try:
        h = json.loads(host.stdout)["details"]["host"].rstrip("/")
    except Exception:
        h = "<host>"
    print(f"{h}/dashboardsv3/{dash['dashboard_id']}/published")


if __name__ == "__main__":
    main()
