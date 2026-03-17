# Genie Watchtower

An AI/BI Lakeview dashboard for monitoring Genie Space adoption, engagement, and operational health across a Databricks account.

## Overview

Genie Watchtower provides account-wide visibility into how Genie Spaces are being used, who's using them, and how well they're performing. Built entirely on system tables — no custom data pipelines required.

## Architecture

**Data Sources:**
- `system.access.audit` — User activity, space interactions, conversations, feedback
- `system.query.history` — Query execution metrics, latency, cost attribution
- `system.billing.usage` + `system.billing.list_prices` — Dollar cost attribution
- `genie_monitoring.dashboard.*` — Pre-built SQL views for common aggregations

**Dashboard Pages (11 tabs):**

| Page | Purpose |
|------|---------|
| **Executive Summary** | Account-wide KPIs, activity trends, cost overview |
| **Workspace Overview** | Per-workspace drill-down with filters |
| **All Spaces** | Space explorer with workspace + space filters |
| **Growth & Trends** | Period-over-period comparisons, fastest growing workspaces |
| **Cost Attribution** | SQL warehouse spend breakdown by workspace |
| **Genie vs. Dashboards** | Workload comparison — Genie vs AI/BI Dashboards vs notebooks |
| **Showback** | Per-user consumption for cost attribution |
| **Engagement & Retention** | User engagement depth, conversation depth, stickiness, satisfaction trends |
| **Health Metrics** | DAU/WAU/MAU, login-to-use, repeat rate, latency, fail rate, stale spaces |
| **About This Dashboard** | Documentation page |

## Health Metrics KPIs

| KPI | Source | Description |
|-----|--------|-------------|
| DAU / 7d / 28d Active Users | `system.access.audit` | Distinct users who asked Genie questions |
| Login-to-Use Rate | `system.access.audit` | % of users who viewed a space and actually asked a question |
| 7-Day Repeat Rate | `system.access.audit` | % of users who return within 7 days |
| Query Fail Rate | `system.query.history` | Proxy for unanswered rate (failed SQL executions) |
| P50 Response Latency | `system.query.history` | Median SQL execution time (not full LLM response time) |
| Query Cost | `system.query.history` | Total compute hours for Genie queries |
| Total Spaces | `system.access.audit` | Distinct spaces with any question activity |
| Stale Spaces | `system.access.audit` | Spaces with no activity in 30+ days |

## Deployment

**Workspace:** Deployed to `adb-2338896885246877.17.azuredatabricks.net`

**Dashboard ID:** `01f11caf09151fedb1919ea8408dffea`

**Warehouse:** `e69629978b5a7e40` (Shared Endpoint)

### Deploy from JSON

```bash
# Update dashboard
databricks api patch /api/2.0/lakeview/dashboards/01f11caf09151fedb1919ea8408dffea \
  --json @dashboard-payload.json \
  --profile intermountain-genie

# Publish
databricks api post /api/2.0/lakeview/dashboards/01f11caf09151fedb1919ea8408dffea/published \
  --json '{"embed_credentials": true, "warehouse_id": "e69629978b5a7e40"}' \
  --profile intermountain-genie
```

## Prerequisites

- Databricks CLI authenticated with `intermountain-genie` profile
- Access to `system.access.audit`, `system.query.history`, `system.billing.usage`
- SQL warehouse with access to system tables

## Files

- `dashboard.json` — Serialized Lakeview dashboard definition (datasets + pages + widgets)
