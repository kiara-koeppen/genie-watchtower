# Genie Watchtower

An AI/BI Lakeview dashboard for monitoring Genie Space adoption, engagement, and operational health across a Databricks account.

## Overview

Genie Watchtower provides account-wide visibility into how Genie Spaces are being used, who's using them, and how well they're performing. Built on system tables with an optional benchmark score collector notebook.

## Architecture

**Data Sources:**
- `system.access.audit` -- User activity, space interactions, conversations, feedback
- `system.query.history` -- Query execution metrics, latency, cost attribution
- `system.billing.usage` + `system.billing.list_prices` -- Dollar cost attribution via proportional method
- `system.access.workspaces_latest` -- Workspace name resolution
- `genie_monitoring.benchmark.*` -- Benchmark scores (populated by the collector notebook)

**Key Datasets:**
- `ds_genie_questions` -- Event-level, one row per question. Supports COUNT, COUNT DISTINCT in widgets. Global date + workspace + space filters all bind to this.
- `ds_genie_feedback` -- Event-level, one row per feedback. AVG(is_positive) = satisfaction rate.
- `ds_genie_cost` -- Daily per workspace. Proportional cost: warehouse_cost * (genie_ms / total_ms).
- `ds_genie_daily_ratios` -- Daily pre-computed ratios (cost/question, Q/user, cost/user). Widgets use AVG.

**Dashboard Pages (10 tabs):**

| Page | Audience | Filters | Purpose |
|------|----------|---------|---------|
| **Executive Summary** | VP | Date range | 12 KPIs, trends, breakdowns -- 1000ft view |
| **Workspace Overview** | Director | Date + Workspace | Same KPIs scoped to workspace, top users, space inventory |
| **All Spaces** | Manager | Date + Workspace + Space | Deepest dive, full space table with all metrics |
| **Growth & Trends** | Analyst | Date + Period | Period-over-period comparisons |
| **Cost Attribution** | Finance | -- | SQL warehouse spend by workspace |
| **Showback** | Finance | -- | Per-user consumption |
| **Engagement & Retention** | Analyst | -- | Conversation depth, stickiness, satisfaction trends |
| **Health Metrics** | Ops | -- | DAU/WAU/MAU, repeat rate, latency trends |
| **About This Dashboard** | Everyone | -- | KPI definitions and methodology |

## KPIs (Executive Summary)

All KPIs respect the global date range filter.

| KPI | Source | Calculation |
|-----|--------|-------------|
| Total Spaces | `ds_genie_questions` | COUNT(DISTINCT space_id) |
| Active Users | `ds_genie_questions` | COUNT(DISTINCT email) |
| Questions Asked | `ds_genie_questions` | COUNT(*) |
| Genie Cost ($) | `ds_genie_cost` | SUM(genie_cost_usd) -- proportional billing attribution |
| Compute Hours | `ds_genie_cost` | SUM(compute_hours) |
| Avg Cost / Space | `ds_genie_daily_ratios` | AVG(daily cost / daily spaces) |
| Satisfaction | `ds_genie_feedback` | AVG(is_positive) where 1=thumbs up, 0=thumbs down |
| Stale Spaces | `ds_stale_spaces` | Spaces with no activity 30+ days (always relative to today) |
| Active Days | `ds_genie_questions` | COUNT(DISTINCT event_date) |
| Avg Q / User / Day | `ds_genie_daily_ratios` | AVG(daily questions / daily users) |
| Avg Cost / Question | `ds_genie_daily_ratios` | AVG(daily cost / daily questions) |
| Avg Cost / User / Day | `ds_genie_daily_ratios` | AVG(daily cost / daily users) |

## Benchmark Score Collection

The `notebooks/collect_benchmark_scores.py` notebook uses the Databricks SDK to pull Genie Space benchmark evaluation results into Delta tables.

**How it works:**
1. Lists all Genie Spaces via `w.genie.list_spaces()`
2. For each space, calls `w.genie.genie_list_eval_runs()` to get benchmark runs
3. For completed runs, calls `w.genie.genie_list_eval_results()` for per-question results
4. Writes to `genie_monitoring.benchmark.genie_eval_runs` and `genie_monitoring.benchmark.genie_eval_results`

**Prerequisites:**
- Benchmark questions must be configured in the Genie Space UI first
- Benchmarks must be run via the UI (the notebook collects results, doesn't trigger runs)
- `databricks-sdk` installed on the cluster

**Schedule:** Run as a daily/weekly job after benchmarks are executed.

## Deployment

**Workspace:** `adb-2338896885246877.17.azuredatabricks.net`
**Dashboard ID:** `01f11caf09151fedb1919ea8408dffea`
**Warehouse:** `e69629978b5a7e40` (Shared Endpoint)
**CLI Profile:** `intermountain-genie`

### Deploy Dashboard

```bash
# Create payload
python3 -c "
import json
with open('dashboard.json') as f: sd = json.load(f)
payload = {'display_name': 'Genie Watchtower', 'warehouse_id': 'e69629978b5a7e40', 'serialized_dashboard': json.dumps(sd)}
with open('/tmp/payload.json', 'w') as f: json.dump(payload, f)
"

# Update + publish
databricks api patch /api/2.0/lakeview/dashboards/01f11caf09151fedb1919ea8408dffea \
  --json @/tmp/payload.json --profile intermountain-genie

databricks api post /api/2.0/lakeview/dashboards/01f11caf09151fedb1919ea8408dffea/published \
  --json '{"embed_credentials": true, "warehouse_id": "e69629978b5a7e40"}' \
  --profile intermountain-genie
```

### Import Benchmark Notebook

Upload `notebooks/collect_benchmark_scores.py` to the workspace and run with parameters:
- `catalog`: target catalog (default: `genie_monitoring`)
- `schema`: target schema (default: `benchmark`)

## Files

```
genie-watchtower/
  dashboard.json                          # Lakeview dashboard definition
  notebooks/
    collect_benchmark_scores.py           # Benchmark score collector (Databricks notebook)
  README.md
```

## Prerequisites

- Databricks CLI authenticated with `intermountain-genie` profile
- Access to system tables: `system.access.audit`, `system.query.history`, `system.billing.usage`, `system.billing.list_prices`
- SQL warehouse with system table access
- For benchmarks: `databricks-sdk` on cluster, benchmark questions configured in Genie UI
