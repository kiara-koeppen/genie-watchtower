# Genie Watchtower

An AI/BI dashboard for monitoring **Genie adoption, answer quality, cost and catalog hygiene** across a Databricks account, packaged as a Databricks Asset Bundle (DAB).

Leadership gets a one-page executive view. Platform and COE teams can drill into any workspace or any Genie agent (formerly Genie space) to see who uses it, how well it answers, what it costs, and whether it needs attention.

## What's in the dashboard

| Page | Audience | What it answers |
|---|---|---|
| **Executive Summary** | Leadership | Active users, questions, active agents, total cost, cost per user, positive feedback; change vs prior 30 days, retention, stickiness, SQL success, workspace coverage; monthly adoption and cost trends; top 15 agents; workspace leaderboard; agent health mix; projected cost once free Genie usage ends |
| **Workspace Deep Dive** | Workspace owners | Pick a workspace: the same KPIs scoped to it, daily users, questions by channel (UI vs API/MCP), every agent with health and "why", users and segments, cost by agent |
| **Agent Deep Dive** | Agent owners, COE | Pick an agent: users, questions, conversation depth, cost per question, SQL success, empty-result rate, median/p95 SQL time, feedback, curation edits, profile (creator, created, last used, last edited), usage by hour and weekday, top users |
| **Quality & Trust** | COE | Weekly SQL outcomes, feedback and answer comments; agent quality scorecard |
| **Cost & Chargeback** | FinOps | Genie billable vs SQL compute vs free usage value; chargeback by workspace, agent and user for any date range; cost by channel |
| **Users & Retention** | Enablement | Active and new users, retention by monthly cohort, user segments (Power / Regular / Casual / New / Dormant), user directory |
| **Governance & Hygiene** | Platform team | Agent inventory with health, idle days, last edited, creator; agents by workspace and creator; curation and benchmark activity |
| **About & Definitions** | Everyone | Sources, refresh, and the definition of every KPI |

All KPIs cover the **last 30 complete days** compared with the 30 days before; trends cover 13 months. KPI definitions live on the last page of the dashboard (and in `watchtower/datasets.py`).

## Architecture

```
 system.access.audit ─────┐
 system.query.history ────┤   genie_watchtower_refresh job (daily, serverless)
 system.billing.usage ────┼──▶ 1. Genie API ─▶ genie_agent_dim         (agent names)
 system.billing.list_prices┤   2. one scan per system table ─▶ wt_base_*   (9 tables)
 system.access.workspaces ┘   3. wt_base_* ─▶ wt_*                     (12 KPI tables)
                                                   │
                                                   ▼
                                   Genie Watchtower AI/BI dashboard
                                   (reads only the small wt_* tables)
```

**Why a job instead of querying system tables live?** Measured on real workspaces, live system-table queries took 4 to 6 minutes per dataset, even on a small account, mostly from scanning query history and billing for warehouse cost attribution. Pre-computing daily makes the dashboard load instantly. A full refresh took about 10 minutes on a very large shared demo account.

**Where the numbers come from**

| Area | Source |
|---|---|
| Questions, conversations, feedback, comments, curation edits, benchmark runs, agent create/trash | `system.access.audit` (`service_name = 'aibiGenie'`) |
| Genie-generated SQL: success, failures, empty results, latency | `system.query.history` (`query_source.genie_space_id`) |
| Genie pay-as-you-go cost by agent, user and channel; free usage | `system.billing.usage` (`billing_origin_product = 'GENIE'`, `usage_metadata.genie.*`) at list price |
| SQL warehouse compute used by Genie (estimate) | Each warehouse's daily list-price cost, split by Genie's share of that warehouse's query time |
| Agent names | Genie API (`GET /api/2.0/genie/spaces`), falling back to the latest name in the audit log |

Costs are **list price**; negotiated discounts are not reflected. Billing corrections (`RETRACTION` / `RESTATEMENT` rows) net out automatically.

## Prerequisites

- Databricks CLI recent enough to support `dataset_catalog` / `dataset_schema` on bundle dashboards (tested with v1.19.0; check with `databricks bundle schema | grep dataset_catalog`).
- A workspace with **Unity Catalog** and these system schemas enabled: `system.access`, `system.billing`, `system.query`.
- The deploying identity (and the job's run-as identity) needs:
  - `SELECT` on the system tables above
  - `USE CATALOG` on the target catalog and `CREATE SCHEMA` (or an existing schema with `CREATE TABLE`)
  - permission to list Genie agents (it sees the agents it has access to)
- A **SQL warehouse** for the dashboard. A 2X-Small serverless warehouse is plenty because the dashboard only reads small tables.
- Serverless jobs compute enabled (the refresh job uses a serverless environment).

## Deploy

```bash
git clone https://github.com/kiara-koeppen/genie-watchtower.git
cd genie-watchtower

# 1. Check the bundle against your workspace
databricks bundle validate -t prod --profile <profile> \
  --var="warehouse_id=<sql_warehouse_id>" \
  --var="catalog=<catalog>" --var="schema=genie_watchtower"

# 2. Deploy the job and the dashboard
databricks bundle deploy -t prod --profile <profile> \
  --var="warehouse_id=<sql_warehouse_id>" \
  --var="catalog=<catalog>" --var="schema=genie_watchtower"

# 3. Run the first refresh (about 5 to 15 minutes depending on account size)
databricks bundle run genie_watchtower_refresh -t prod --profile <profile> \
  --var="warehouse_id=<sql_warehouse_id>" \
  --var="catalog=<catalog>" --var="schema=genie_watchtower"
```

Then open **Dashboards → Genie Watchtower**, publish it, and share it. The job refreshes daily at 6:00 AM (America/Denver) by default.

**Targets:** `dev` (default) deploys under your user folder with a `[dev <you>]` prefix and a paused schedule. `prod` deploys to `/Workspace/Shared/.bundle/genie_watchtower/prod` with the schedule running.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `warehouse_id` | (required) | SQL warehouse the dashboard runs on |
| `catalog` | (required) | Catalog for `genie_agent_dim`, `wt_base_*` and `wt_*` tables (must exist). These tables contain user emails and usage, so choose a catalog only the intended audience can read |
| `schema` | `genie_watchtower` | Schema for those tables (created if missing) |
| `extra_workspace_hosts` | `""` | Comma-separated workspace URLs to also pull agent names from. Without it, agents in other workspaces show their audit-log name or "Unnamed agent ..." |
| `refresh_schedule` | `0 0 6 * * ?` | Quartz cron for the refresh job |
| `refresh_timezone` | `America/Denver` | Time zone for the schedule |

Variables can also be set per target in `databricks.yml` instead of on the command line. Pass each `--var` as its own argument (don't pack several into one shell variable: zsh won't split it, and the values end up garbled).

**Access:** share the dashboard (and grant `SELECT` on the schema) only with the people who should see Genie usage at the user level. The dashboard runs queries as the viewer (`embed_credentials: false`).

## Repository layout

```
databricks.yml                       Bundle definition: variables and dev/prod targets
resources/
  genie_watchtower.job.yml           Daily serverless refresh job
  genie_watchtower.dashboard.yml     Dashboard resource (points datasets at catalog.schema)
src/                                 Everything that runs in the workspace (generated, do not hand-edit SQL)
  genie_watchtower.lvdash.json       The AI/BI dashboard (8 pages, 12 datasets)
  notebooks/refresh_watchtower.py    Refresh notebook: agent names, base tables, KPI tables
  sql/NN_base_*.sql                  One scan of each system table into wt_base_*
  sql/ds_*.sql                       KPI tables built from wt_base_*
watchtower/                          Source of truth (Python) for KPIs and layout
  datasets.py                        Every KPI as SQL, with definitions and windows
  build.py                           Dashboard layout (6-column grid) and generator for src/
tests/
  test_layout.py                     Offline: grid fit, no overlaps, fields exist, filters wired
  test_datasets_live.py              Live: runs against real wt_* tables, checks invariants
scripts/
  sql.py                             Run SQL on a warehouse from the CLI
  deploy.py                          Dev helper: publish outside the bundle
```

## Development

Edit KPIs in `watchtower/datasets.py` or layout in `watchtower/build.py`, then regenerate and test:

```bash
uv venv .venv && uv pip install --python .venv/bin/python pytest
.venv/bin/python -m watchtower.build          # regenerates src/genie_watchtower.lvdash.json and src/sql/
.venv/bin/python -m pytest -q                 # offline layout tests

# Live checks against tables written by a refresh run
WATCHTOWER_PROFILE=<profile> WATCHTOWER_WAREHOUSE=<warehouse_id> WATCHTOWER_TARGET=<catalog>.<schema> \
  .venv/bin/python -m pytest -q -m live
```

`test_committed_bundle_files_are_current` fails if `src/` is out of date with the builder, so regenerate before committing.

## Known limits

- **Agent names across workspaces:** the Genie API is per workspace. Set `extra_workspace_hosts` (and give the job identity access) to name agents everywhere.
- **Region scope:** coverage follows what the account's system tables expose for this metastore's region.
- **SQL compute cost is an estimate** (proportional by query time). Genie pay-as-you-go cost is exact at list price.
- **Free usage value** prices free Genie usage at the list price of the account's billable Genie SKU, to show what the same usage would cost once free usage ends.
- **Drill-down pages** open on "All" until you pick a workspace or agent; KPI tiles show a single workspace or agent's values once one is selected.
