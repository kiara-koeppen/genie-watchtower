"""Live tests: run every dataset against a real workspace's system tables.

Requires WATCHTOWER_PROFILE / WATCHTOWER_WAREHOUSE (defaults to the e2-demo dev setup).
Run with: pytest -m live
"""
import pytest

import os

from scripts.sql import run_sql
from watchtower.datasets import DATASETS, dataset

pytestmark = pytest.mark.live

# Materialized tables written by notebooks/refresh_watchtower.py (catalog.schema).
TARGET = os.environ.get("WATCHTOWER_TARGET", "users.kiara_koeppen")


@pytest.fixture(scope="module")
def results():
    out = {}
    for d in DATASETS:
        cols, rows = run_sql(f"SELECT * FROM {TARGET}.wt_{d.name.removeprefix('ds_')}")
        out[d.name] = [dict(zip(cols, r)) for r in rows], cols
    return out


@pytest.mark.parametrize("name", [d.name for d in DATASETS])
def test_dataset_returns_declared_columns(results, name):
    _, cols = results[name]
    assert cols == list(dataset(name).columns)


@pytest.mark.parametrize("name", [d.name for d in DATASETS if not d.may_be_empty])
def test_dataset_not_empty(results, name):
    rows, _ = results[name]
    assert rows, f"{name} returned no rows"


def _num(v):
    return None if v is None else float(v)


def test_exec_kpis_single_row_and_sane(results):
    rows, _ = results["ds_exec_kpis"]
    assert len(rows) == 1
    r = {k: _num(v) if k not in ("as_of",) else v for k, v in rows[0].items()}
    assert r["active_users"] > 0
    assert r["active_users"] <= r["users_all_time"]
    assert 0 <= r["user_retention_rate"] <= 1
    assert 0 <= r["stickiness_dau_mau"] <= 1
    assert 0 <= r["sql_success_rate"] <= 1
    assert r["positive_feedback_rate"] is None or 0 <= r["positive_feedback_rate"] <= 1
    assert r["workspaces_with_genie"] <= r["workspaces_total"]
    for c in ("billable_cost_usd", "free_usage_value_usd", "sql_compute_cost_usd", "total_cost_usd"):
        assert r[c] >= 0, c
    assert abs(r["total_cost_usd"] - (r["billable_cost_usd"] + r["sql_compute_cost_usd"])) < 0.05


def test_exec_matches_workspace_rollup(results):
    """Questions in the exec KPI must equal the sum across workspaces (no double counting)."""
    exec_q = float(results["ds_exec_kpis"][0][0]["questions"])
    ws_q = sum(float(r["questions"]) for r in results["ds_workspace_kpis"][0])
    assert exec_q == ws_q


def test_agent_rollup_matches_exec(results):
    exec_q = float(results["ds_exec_kpis"][0][0]["questions"])
    ag_q = sum(float(r["questions"] or 0) for r in results["ds_agent_kpis"][0])
    assert exec_q == ag_q


def test_agent_ratios_bounded(results):
    for r in results["ds_agent_kpis"][0]:
        for c in ("sql_success_rate", "positive_feedback_rate", "empty_result_rate"):
            v = _num(r[c])
            assert v is None or 0 <= v <= 1, (r["agent_id"], c, v)
        assert r["health_status"] in {"Healthy", "New", "Growing", "Declining", "Needs attention", "Stale", "Unused", "Trashed"}


def test_agent_labels_unique(results):
    labels = [r["agent_label"] for r in results["ds_agent_kpis"][0]]
    assert len(labels) == len(set(labels)), "agent_label must be unique for the drill-down filter"


def test_costs_non_negative(results):
    for r in results["ds_cost_daily"][0]:
        assert float(r["billable_cost_usd"]) >= -0.01
        assert float(r["free_usage_value_usd"]) >= -0.01


def test_monthly_trend_ordered_months(results):
    months = [r["month"] for r in results["ds_monthly_trend"][0]]
    assert months == sorted(months) and len(months) >= 2


def test_user_segments_valid(results):
    for r in results["ds_user_kpis"][0]:
        assert r["user_segment"] in {"Power", "Regular", "Casual", "New", "Dormant"}
