# Databricks notebook source
# MAGIC %md
# MAGIC # Genie Space Benchmark Score Collector
# MAGIC
# MAGIC Iterates all Genie Spaces in the account, pulls benchmark evaluation runs and results
# MAGIC via the Databricks SDK, and writes them to Delta tables for dashboard consumption.
# MAGIC
# MAGIC **Data Sources:** Databricks SDK (`w.genie.*` methods)
# MAGIC **Outputs:**
# MAGIC - `{catalog}.{schema}.genie_eval_runs` — One row per evaluation run per space
# MAGIC - `{catalog}.{schema}.genie_eval_results` — One row per question result per run
# MAGIC
# MAGIC **Schedule:** Run daily or weekly to keep benchmark scores current.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

dbutils.widgets.text("catalog", "genie_monitoring", "Target Catalog")
dbutils.widgets.text("schema", "benchmark", "Target Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

print(f"Writing to: {catalog}.{schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Initialize SDK Client

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from datetime import datetime
import time

w = WorkspaceClient()
print(f"Connected to: {w.config.host}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Discover All Genie Spaces
# MAGIC
# MAGIC Lists spaces visible to the current user. For account-wide coverage,
# MAGIC run this notebook as a service principal with access to all workspaces.

# COMMAND ----------

spaces_resp = w.genie.list_spaces()
spaces = spaces_resp.spaces if hasattr(spaces_resp, 'spaces') and spaces_resp.spaces else []
print(f"Found {len(spaces)} Genie Spaces")

for s in spaces:
    print(f"  {s.space_id}: {s.title}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Collect Evaluation Runs
# MAGIC
# MAGIC For each space, check if any benchmark evaluation runs exist.
# MAGIC Evaluation runs contain the overall accuracy score.

# COMMAND ----------

eval_runs = []
spaces_with_evals = 0

for space in spaces:
    try:
        runs_resp = w.genie.genie_list_eval_runs(space_id=space.space_id)
        runs = runs_resp.eval_runs if hasattr(runs_resp, 'eval_runs') and runs_resp.eval_runs else []

        if runs:
            spaces_with_evals += 1
            print(f"\n{space.title} ({space.space_id}): {len(runs)} eval run(s)")

            for run in runs:
                run_data = {
                    "space_id": space.space_id,
                    "space_title": space.title,
                    "collected_at": datetime.utcnow().isoformat(),
                }

                # Extract all available fields from the run object
                for attr in dir(run):
                    if not attr.startswith('_') and not callable(getattr(run, attr, None)):
                        val = getattr(run, attr, None)
                        if val is not None:
                            run_data[attr] = str(val) if not isinstance(val, (int, float, bool, str)) else val

                eval_runs.append(run_data)
                print(f"  Run {run_data.get('eval_run_id', 'unknown')}: status={run_data.get('status', '?')}")
    except Exception as e:
        # Skip spaces we can't access or that don't support evals
        err = str(e)
        if 'not found' not in err.lower() and 'permission' not in err.lower():
            print(f"  {space.title}: {err[:100]}")

print(f"\nSummary: {spaces_with_evals} spaces with evaluations, {len(eval_runs)} total runs")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Collect Evaluation Results (Per-Question Details)
# MAGIC
# MAGIC For each completed evaluation run, pull the per-question results
# MAGIC including rating (Good/Bad/Manual Review), generated SQL, and match type.

# COMMAND ----------

eval_results = []

for run_data in eval_runs:
    run_id = run_data.get('eval_run_id')
    space_id = run_data.get('space_id')
    status = run_data.get('status', '')

    # Only pull results for completed runs
    if not run_id or 'COMPLETED' not in str(status).upper():
        print(f"  Skipping run {run_id} (status: {status})")
        continue

    try:
        page_token = None
        page_num = 0

        while True:
            results_resp = w.genie.genie_list_eval_results(
                space_id=space_id,
                eval_run_id=run_id,
                page_size=100,
                page_token=page_token
            )

            results = results_resp.results if hasattr(results_resp, 'results') and results_resp.results else []

            for result in results:
                result_data = {
                    "space_id": space_id,
                    "space_title": run_data.get('space_title', ''),
                    "eval_run_id": run_id,
                    "collected_at": datetime.utcnow().isoformat(),
                }

                for attr in dir(result):
                    if not attr.startswith('_') and not callable(getattr(result, attr, None)):
                        val = getattr(result, attr, None)
                        if val is not None:
                            result_data[attr] = str(val) if not isinstance(val, (int, float, bool, str)) else val

                eval_results.append(result_data)

            page_num += 1
            page_token = results_resp.page_token if hasattr(results_resp, 'page_token') else None

            if not page_token or not results:
                break

        print(f"  Run {run_id}: collected {page_num} page(s) of results")

    except Exception as e:
        print(f"  Run {run_id}: error collecting results - {str(e)[:100]}")

print(f"\nTotal results collected: {len(eval_results)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write to Delta Tables
# MAGIC
# MAGIC Creates the target catalog/schema if needed, then writes the collected data
# MAGIC as Delta tables with merge-on-write (upsert) to handle incremental runs.

# COMMAND ----------

# Create catalog and schema if they don't exist
spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
print(f"Ensured {catalog}.{schema} exists")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Write Evaluation Runs

# COMMAND ----------

if eval_runs:
    df_runs = spark.createDataFrame(eval_runs)

    # Write with overwrite for simplicity (full refresh each run)
    df_runs.write.format("delta").mode("overwrite").saveAsTable(
        f"{catalog}.{schema}.genie_eval_runs"
    )
    print(f"Wrote {len(eval_runs)} eval runs to {catalog}.{schema}.genie_eval_runs")
    display(df_runs)
else:
    print("No evaluation runs found. Table not created.")
    print("To generate benchmark data:")
    print("  1. Open a Genie Space in the UI")
    print("  2. Go to the Benchmark tab")
    print("  3. Add benchmark questions with SQL answers")
    print("  4. Click 'Run Benchmark'")
    print("  5. Re-run this notebook after the benchmark completes")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Write Evaluation Results

# COMMAND ----------

if eval_results:
    df_results = spark.createDataFrame(eval_results)

    df_results.write.format("delta").mode("overwrite").saveAsTable(
        f"{catalog}.{schema}.genie_eval_results"
    )
    print(f"Wrote {len(eval_results)} eval results to {catalog}.{schema}.genie_eval_results")
    display(df_results)
else:
    print("No evaluation results found. Table not created.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC ### What was collected:

# COMMAND ----------

print(f"Spaces discovered:      {len(spaces)}")
print(f"Spaces with benchmarks: {spaces_with_evals}")
print(f"Evaluation runs:        {len(eval_runs)}")
print(f"Question-level results: {len(eval_results)}")
print(f"Output tables:          {catalog}.{schema}.genie_eval_runs")
print(f"                        {catalog}.{schema}.genie_eval_results")

if spaces_with_evals == 0:
    print("\n--- No benchmarks found ---")
    print("This is expected if no Genie Spaces have benchmark questions configured.")
    print("The notebook will automatically pick up results once benchmarks are run via the UI.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next Steps
# MAGIC
# MAGIC Once this notebook has collected benchmark data:
# MAGIC
# MAGIC 1. **Add a dashboard dataset** pointing to `genie_eval_runs` for overall accuracy scores
# MAGIC 2. **Add a dashboard dataset** pointing to `genie_eval_results` for per-question breakdowns
# MAGIC 3. **Schedule this notebook** as a daily/weekly job to keep scores current
# MAGIC
# MAGIC ### Dashboard Query Examples
# MAGIC
# MAGIC **Overall benchmark score per space:**
# MAGIC ```sql
# MAGIC SELECT space_title, accuracy, status, created_at
# MAGIC FROM genie_monitoring.benchmark.genie_eval_runs
# MAGIC WHERE status = 'COMPLETED'
# MAGIC ORDER BY created_at DESC
# MAGIC ```
# MAGIC
# MAGIC **Per-question accuracy breakdown:**
# MAGIC ```sql
# MAGIC SELECT space_title, rating, COUNT(*) AS question_count
# MAGIC FROM genie_monitoring.benchmark.genie_eval_results
# MAGIC GROUP BY space_title, rating
# MAGIC ```
