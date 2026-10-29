# Databricks notebook source
# MAGIC %md
# MAGIC # Genie Watchtower: refresh
# MAGIC
# MAGIC Schedule this notebook daily (a serverless job is fine). It does two things:
# MAGIC
# MAGIC 1. **Agent names.** System tables only record an agent's name when it is renamed, so most
# MAGIC    agents would show as "Unnamed". This step lists Genie agents through the Genie API and writes
# MAGIC    `genie_agent_dim` (agent id, title, description, warehouse, workspace).
# MAGIC 2. **Materialize KPIs (optional).** For large accounts, each dashboard dataset is written to a
# MAGIC    small `wt_*` table so the dashboard loads instantly instead of scanning system tables on every
# MAGIC    view. Use `dashboard_materialized.json` with this mode.
# MAGIC
# MAGIC The SQL lives in `../sql/` and is generated from `watchtower/datasets.py`
# MAGIC (`python -m watchtower.build --target __TARGET__` keeps it portable across catalogs).

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parameters
# MAGIC - **catalog / schema**: where `genie_agent_dim` and the `wt_*` tables are written.
# MAGIC - **materialize**: `true` to also write the `wt_*` KPI tables.
# MAGIC - **extra_workspace_hosts**: optional comma-separated workspace URLs to also pull agent names from
# MAGIC   (the job identity needs access to each). Leave blank for this workspace only.

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "genie_watchtower", "Schema")
dbutils.widgets.dropdown("materialize", "false", ["true", "false"], "Materialize KPI tables")
dbutils.widgets.text("extra_workspace_hosts", "", "Extra workspace URLs (comma-separated)")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
materialize = dbutils.widgets.get("materialize") == "true"
extra_hosts = [h.strip() for h in dbutils.widgets.get("extra_workspace_hosts").split(",") if h.strip()]

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`")
spark.sql(f"USE `{catalog}`.`{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Refresh `genie_agent_dim`
# MAGIC Pages through `GET /api/2.0/genie/spaces` for this workspace (and any extra workspaces), then
# MAGIC replaces the table. Agents the caller can't see are simply absent; the dashboard falls back to
# MAGIC audit-log names for those.

# COMMAND ----------

from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from pyspark.sql import types as T


def list_agents(client):
    """Yield every Genie agent visible to this identity in the client's workspace."""
    token = None
    while True:
        query = {"page_size": 100}
        if token:
            query["page_token"] = token
        resp = client.api_client.do("GET", "/api/2.0/genie/spaces", query=query)
        yield from resp.get("spaces", [])
        token = resp.get("next_page_token")
        if not token:
            return


clients = [WorkspaceClient()] + [WorkspaceClient(host=h) for h in extra_hosts]
rows, refreshed_at = [], datetime.now(timezone.utc)
for c in clients:
    ws_id = str(c.get_workspace_id())
    for s in list_agents(c):
        rows.append((s["space_id"], s.get("title"), s.get("description"), s.get("warehouse_id"), ws_id,
                     s.get("create_time"), s.get("update_time"), refreshed_at))
    print(f"{c.config.host}: {sum(1 for r in rows if r[4] == ws_id)} agents")

schema_def = T.StructType([
    T.StructField("agent_id", T.StringType(), False, {"comment": "Genie agent (space) id"}),
    T.StructField("title", T.StringType(), True, {"comment": "Agent display name"}),
    T.StructField("description", T.StringType(), True, {"comment": "Agent description"}),
    T.StructField("warehouse_id", T.StringType(), True, {"comment": "SQL warehouse the agent uses"}),
    T.StructField("workspace_id", T.StringType(), True, {"comment": "Workspace that owns the agent"}),
    T.StructField("create_time", T.StringType(), True, {"comment": "Agent creation time (API)"}),
    T.StructField("update_time", T.StringType(), True, {"comment": "Agent last update time (API)"}),
    T.StructField("refreshed_at", T.TimestampType(), False, {"comment": "When this row was refreshed"}),
])
(spark.createDataFrame(rows, schema_def)
 .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("genie_agent_dim"))
spark.sql("COMMENT ON TABLE genie_agent_dim IS 'Genie agent names for Genie Watchtower, from the Genie API'")
print(f"genie_agent_dim: {len(rows)} agents")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Materialize KPI tables (optional)
# MAGIC Runs each generated dataset query and writes it to `wt_<dataset>`. Skipped unless
# MAGIC `materialize = true`.

# COMMAND ----------

import pathlib

if materialize:
    nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    sql_dir = pathlib.Path("/Workspace" + nb_path).parent.parent / "sql"
    # NN_base_*.sql first (one scan of each system table), then ds_*.sql (reads only wt_base_*).
    files = sorted(sql_dir.glob("[0-9][0-9]_base_*.sql")) + sorted(sql_dir.glob("ds_*.sql"))
    for f in files:
        stem = f.stem.split("_", 1)[1] if f.stem[:2].isdigit() else f.stem.removeprefix("ds_")
        table = f"wt_{stem}"
        sql = f.read_text().replace("__TARGET__", f"`{catalog}`.`{schema}`")
        spark.sql(f"CREATE OR REPLACE TABLE {table} AS {sql}")
        print(f"{table}: {spark.table(table).count()} rows")
else:
    print("materialize = false: dashboard reads system tables live")
