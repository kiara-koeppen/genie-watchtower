"""Offline structural tests for the generated dashboard (no workspace needed)."""
import json
import pathlib

import pytest

from watchtower.build import build_dashboard
from watchtower.datasets import DATASETS

GRID_COLUMNS = 6
EXPECTED_PAGES = [
    "Executive Summary",
    "Workspace Deep Dive",
    "Agent Deep Dive",
    "Quality & Trust",
    "Cost & Chargeback",
    "Users & Retention",
    "Governance & Hygiene",
    "About & Definitions",
]


@pytest.fixture(scope="module")
def dash():
    return build_dashboard()


def _canvas_pages(dash):
    return [p for p in dash["pages"] if p.get("pageType") == "PAGE_TYPE_CANVAS"]


def test_pages_in_expected_order(dash):
    assert [p["displayName"] for p in _canvas_pages(dash)] == EXPECTED_PAGES


def test_every_dataset_is_declared_once(dash):
    names = [d["name"] for d in dash["datasets"]]
    assert len(names) == len(set(names))
    assert set(names) == {d.name for d in DATASETS}


def test_widgets_fit_six_column_grid(dash):
    for page in dash["pages"]:
        for item in page["layout"]:
            pos = item["position"]
            assert pos["x"] >= 0 and pos["width"] >= 1, item["widget"]["name"]
            assert pos["x"] + pos["width"] <= GRID_COLUMNS, item["widget"]["name"]
            assert pos["height"] >= 1


def test_no_overlapping_widgets(dash):
    for page in _canvas_pages(dash):
        cells = {}
        for item in page["layout"]:
            p = item["position"]
            for x in range(p["x"], p["x"] + p["width"]):
                for y in range(p["y"], p["y"] + p["height"]):
                    assert (x, y) not in cells, (
                        f"{page['displayName']}: {item['widget']['name']} overlaps {cells[(x, y)]}")
                    cells[(x, y)] = item["widget"]["name"]


def test_widget_names_unique_per_dashboard(dash):
    names = [i["widget"]["name"] for p in dash["pages"] for i in p["layout"]]
    assert len(names) == len(set(names))


def test_widget_fields_reference_real_columns(dash):
    cols = {d.name: set(d.columns) for d in DATASETS}
    for page in dash["pages"]:
        for item in page["layout"]:
            for q in item["widget"].get("queries", []):
                ds = q["query"]["datasetName"]
                assert ds in cols, f"{item['widget']['name']} -> unknown dataset {ds}"
                for f in q["query"]["fields"]:
                    referenced = {c for c in cols[ds] if f"`{c}`" in f["expression"]}
                    assert referenced, f"{item['widget']['name']}: {f['expression']} uses no column of {ds}"


def test_encodings_point_at_query_fields(dash):
    for page in dash["pages"]:
        for item in page["layout"]:
            w = item["widget"]
            if "queries" not in w:
                continue
            field_names = {f["name"] for q in w["queries"] for f in q["query"]["fields"]}
            enc = json.dumps(w["spec"]["encodings"])
            for fn in json.loads(json.dumps([e for e in _field_names(w["spec"]["encodings"])])):
                assert fn in field_names, f"{w['name']}: encoding {fn} not in query fields"


def _field_names(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "fieldName":
                yield v
            else:
                yield from _field_names(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _field_names(v)


def test_drilldown_pages_have_single_select_filter(dash):
    pages = {p["displayName"]: p for p in dash["pages"]}
    for title, field in [("Workspace Deep Dive", "workspace_name"), ("Agent Deep Dive", "agent_label")]:
        filters = [i["widget"] for i in pages[title]["layout"]
                   if i["widget"].get("spec", {}).get("widgetType") == "filter-single-select"]
        assert filters, f"{title} needs a single-select filter"
        assert any(f["spec"]["encodings"]["fields"][0]["fieldName"] == field for f in filters)


def test_drilldown_filter_reaches_every_dataset_on_page(dash):
    """A page-level filter only applies to datasets it has a query for."""
    pages = {p["displayName"]: p for p in dash["pages"]}
    for title in ["Workspace Deep Dive", "Agent Deep Dive"]:
        layout = pages[title]["layout"]
        used = {q["query"]["datasetName"] for i in layout for q in i["widget"].get("queries", [])
                if not i["widget"]["spec"]["widgetType"].startswith("filter")}
        filtered = {q["query"]["datasetName"] for i in layout for q in i["widget"].get("queries", [])
                    if i["widget"]["spec"]["widgetType"] == "filter-single-select"}
        assert used <= filtered, f"{title}: datasets not wired to filter: {used - filtered}"


def test_no_em_dashes_in_text(dash):
    assert "—" not in json.dumps(dash, ensure_ascii=False)


def test_bundle_dashboard_uses_unqualified_wt_tables():
    """The bundle sets dataset_catalog/schema, so table names must not be qualified."""
    for ds in build_dashboard()["datasets"]:
        assert ds["queryLines"][0] == f"SELECT * FROM wt_{ds['name'].removeprefix('ds_')}", ds["name"]


def test_committed_bundle_files_are_current(dash):
    from watchtower.build import DASHBOARD_FILE, SQL_DIR
    assert json.loads(DASHBOARD_FILE.read_text()) == build_dashboard(), "run: python -m watchtower.build"
    assert len(list(SQL_DIR.glob("*.sql"))) == 9 + len(DATASETS)


def test_sql_files_only_reference_target_placeholder():
    from watchtower.build import SQL_DIR
    for f in SQL_DIR.glob("*.sql"):
        sql = f.read_text()
        assert "users.kiara_koeppen" not in sql and "main.genie_watchtower" not in sql, f.name
