"""Build the Genie Watchtower v2 Lakeview (AI/BI) dashboard JSON.

    python -m watchtower.build                    # writes dashboard.json (uses genie_agent_dim)
    python -m watchtower.build --system-only      # writes dashboard_system_only.json

Layout uses the 6-column AI/BI grid. Each page is described as rows of widgets; the
builder assigns x/y positions so nothing overlaps.
"""
import argparse
import json
import pathlib

from watchtower.datasets import build_datasets

ROOT = pathlib.Path(__file__).resolve().parents[1]

NUM = {"type": "number-plain", "abbreviation": "compact", "decimalPlaces": {"type": "max", "places": 1}}
INT = {"type": "number-plain", "abbreviation": "compact", "decimalPlaces": {"type": "max", "places": 0}}
USD = {"type": "number-currency", "currencyCode": "USD", "abbreviation": "compact",
       "decimalPlaces": {"type": "max", "places": 2}}
PCT = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 1}}

HEALTH_COLORS = [
    {"value": "Healthy", "color": "#00A972"}, {"value": "Growing", "color": "#2272B4"},
    {"value": "Declining", "color": "#FFAB00"}, {"value": "Needs attention", "color": "#FF3621"},
    {"value": "Stale", "color": "#919191"}, {"value": "Unused", "color": "#C4C4C4"},
    {"value": "Trashed", "color": "#5A5A5A"},
]


# ------------------------------------------------------------------ field helpers
def col(c):
    return {"name": c, "expression": f"`{c}`"}


def agg(fn, c):
    name = f"{fn.lower().replace(' ', '')}({c})"
    expr = f"COUNT(DISTINCT `{c}`)" if fn == "countdistinct" else f"{fn.upper()}(`{c}`)"
    return {"name": name, "expression": expr}


def time_bucket(unit, c):
    return {"name": f"{unit.lower()}ly({c})" if unit != "DAY" else f"daily({c})",
            "expression": f'DATE_TRUNC("{unit}", `{c}`)'}


def _query(ds, fields, disaggregated):
    return [{"name": "main_query", "query": {"datasetName": ds, "fields": fields,
                                             "disaggregated": disaggregated}}]


def _frame(title, description=None):
    f = {"showTitle": True, "title": title}
    if description:
        f.update(showDescription=True, description=description)
    return f


# ------------------------------------------------------------------ widget helpers
class W:
    """A widget plus its size; position is assigned by the page builder."""

    def __init__(self, widget, width, height):
        self.widget, self.width, self.height = widget, width, height


def text(name, lines, width=6, height=1):
    return W({"name": name, "multilineTextboxSpec": {"lines": lines}}, width, height)


def counter(name, ds, column, title, fmt=NUM, description=None, width=1, height=2):
    spec = {"version": 2, "widgetType": "counter",
            "encodings": {"value": {"fieldName": column, "displayName": title, "format": fmt}},
            "frame": _frame(title, description)}
    return W({"name": name, "queries": _query(ds, [col(column)], True), "spec": spec}, width, height)


def counter_agg(name, ds, fn, column, title, fmt=NUM, description=None, width=1, height=2):
    f = agg(fn, column)
    spec = {"version": 2, "widgetType": "counter",
            "encodings": {"value": {"fieldName": f["name"], "displayName": title, "format": fmt}},
            "frame": _frame(title, description)}
    return W({"name": name, "queries": _query(ds, [f], False), "spec": spec}, width, height)


def chart(name, kind, ds, x, ys, title, color=None, x_type="temporal", y_fmt=NUM, stacked=False,
          description=None, width=3, height=5, color_mappings=None, x_title=None):
    """x and color are field dicts; ys is a list of (field dict, display name)."""
    fields = [x] + [f for f, _ in ys] + ([color] if color else [])
    enc = {"x": {"fieldName": x["name"], "scale": {"type": x_type}, "displayName": x_title or x["name"]}}
    if len(ys) == 1:
        f, label = ys[0]
        enc["y"] = {"fieldName": f["name"], "scale": {"type": "quantitative"}, "displayName": label,
                    "format": y_fmt}
    else:
        enc["y"] = {"scale": {"type": "quantitative"},
                    "fields": [{"fieldName": f["name"], "displayName": label} for f, label in ys]}
    if color:
        cscale = {"type": "categorical"}
        if color_mappings:
            cscale["mappings"] = color_mappings
        enc["color"] = {"fieldName": color["name"], "scale": cscale, "displayName": color["name"]}
    spec = {"version": 3, "widgetType": kind, "encodings": enc, "frame": _frame(title, description)}
    disagg = all(not f["expression"].startswith(("SUM", "COUNT", "AVG", "MAX", "MIN", "DATE_TRUNC"))
                 for f in fields)
    return W({"name": name, "queries": _query(ds, fields, disagg), "spec": spec}, width, height)


def pie(name, ds, category, value_fn, value_col, title, mappings=None, width=2, height=5):
    v = agg(value_fn, value_col)
    color = {"fieldName": category, "scale": {"type": "categorical"}, "displayName": category}
    if mappings:
        color["scale"]["mappings"] = mappings
    spec = {"version": 3, "widgetType": "pie",
            "encodings": {"angle": {"fieldName": v["name"], "scale": {"type": "quantitative"},
                                    "displayName": value_col},
                          "color": color},
            "frame": _frame(title)}
    return W({"name": name, "queries": _query(ds, [col(category), v], False), "spec": spec}, width, height)


def table(name, ds, columns, title, description=None, width=6, height=6, aggregated=None):
    """columns: list of (column, display). aggregated: dict column->fn for grouped tables."""
    fields, encs = [], []
    for c, label in columns:
        if aggregated and c in aggregated:
            f = agg(aggregated[c], c)
        else:
            f = col(c)
        fields.append(f)
        encs.append({"fieldName": f["name"], "displayName": label})
    spec = {"version": 2, "widgetType": "table", "encodings": {"columns": encs},
            "frame": _frame(title, description)}
    return W({"name": name, "queries": _query(ds, fields, not aggregated), "spec": spec}, width, height)


def filter_widget(name, kind, field, title, datasets, width=2, height=2, default=None):
    queries, encs = [], []
    for ds in datasets:
        qn = f"{name}_{ds}"
        queries.append({"name": qn, "query": {"datasetName": ds, "fields": [col(field)],
                                              "disaggregated": False}})
        encs.append({"fieldName": field, "displayName": title, "queryName": qn})
    spec = {"version": 2, "widgetType": kind, "encodings": {"fields": encs}, "frame": _frame(title)}
    if default is not None:
        spec["selection"] = default
    return W({"name": name, "queries": queries, "spec": spec}, width, height)


def date_filter(name, field, title, datasets, days=30, width=2, height=2):
    default = {"defaultSelection": {"range": {"dataType": "DATETIME", "min": {"value": f"now-{days}d/d"},
                                              "max": {"value": "now/d"}}}}
    return filter_widget(name, "filter-date-range-picker", field, title, datasets, width, height, default)


def page(name, title, rows):
    """rows: list of lists of W. Widgets in a row are placed left to right; the row's
    height is its tallest widget."""
    layout, y = [], 0
    for row in rows:
        x = 0
        for w in row:
            assert x + w.width <= 6, f"{name}: row overflows grid at {w.widget['name']}"
            layout.append({"widget": w.widget, "position": {"x": x, "y": y, "width": w.width,
                                                             "height": w.height}})
            x += w.width
        y += max(w.height for w in row)
    return {"name": name, "displayName": title, "pageType": "PAGE_TYPE_CANVAS", "layout": layout}


def stack(*ws):
    """Combine vertically stacked widgets into one column block for a row."""
    return ws


# ------------------------------------------------------------------ pages
E, M, WS, AG = "ds_exec_kpis", "ds_monthly_trend", "ds_workspace_kpis", "ds_agent_kpis"
DA, Q, C, H, U, R = ("ds_daily_activity", "ds_quality_daily", "ds_cost_daily", "ds_hourly_pattern",
                     "ds_user_kpis", "ds_retention_cohort")
MONTH = col("month")


def exec_page():
    return page("exec", "Executive Summary", [
        [text("exec_title", ["## Genie Watchtower: executive summary",
                             "Genie adoption, value, quality and cost across the account. KPIs cover the "
                             "**last 30 complete days**; changes compare to the 30 days before. "
                             "Definitions are on the last page."], height=2)],
        [counter("e_users", E, "active_users", "Active users", INT, "People who asked Genie a question"),
         counter("e_questions", E, "questions", "Questions asked", INT),
         counter("e_agents", E, "active_agents", "Active agents", INT, "Agents that got at least one question"),
         counter("e_total_cost", E, "total_cost_usd", "Total cost", USD, "Genie billable + SQL compute, list price"),
         counter("e_cpu", E, "cost_per_active_user", "Cost per active user", USD),
         counter("e_fb", E, "positive_feedback_rate", "Positive feedback", PCT)],
        [counter("e_users_chg", E, "active_users_change_pct", "Active users vs prior 30d", PCT),
         counter("e_q_chg", E, "questions_change_pct", "Questions vs prior 30d", PCT),
         counter("e_retention", E, "user_retention_rate", "User retention", PCT,
                 "Last period's users who came back"),
         counter("e_sticky", E, "stickiness_dau_mau", "Stickiness (DAU/MAU)", PCT),
         counter("e_sql", E, "sql_success_rate", "SQL success rate", PCT),
         counter("e_coverage", E, "workspace_coverage_pct", "Workspaces using Genie", PCT)],
        [chart("e_mau_trend", "bar", M, MONTH, [(col("active_users"), "Active users")],
               "Monthly active users", x_title="Month", y_fmt=INT,
               description="Current month is partial"),
         chart("e_q_trend", "line", M, MONTH, [(col("questions"), "Questions"), (col("new_users"), "New users")],
               "Questions and new users by month", x_title="Month")],
        [chart("e_cost_trend", "bar", M, MONTH,
               [(col("billable_cost_usd"), "Genie billable"), (col("sql_compute_cost_usd"), "SQL compute (est.)"),
                (col("free_usage_value_usd"), "Free usage value")],
               "Monthly cost and free usage value", x_title="Month", stacked=True,
               description="Free usage value shows what free Genie usage would cost at list price"),
         table("e_top_agents", AG, [("agent_label", "Agent"), ("workspace_name", "Workspace"),
                                    ("active_users", "Users"), ("questions", "Questions"),
                                    ("positive_feedback_pct", "Positive feedback %"),
                                    ("total_cost_usd", "Cost ($)"), ("health_status", "Health")],
               "Top agents (last 30 days)", width=3, height=5)],
        [table("e_workspaces", WS, [("workspace_name", "Workspace"), ("active_users", "Users"),
                                    ("questions", "Questions"), ("active_agents", "Active agents"),
                                    ("share_of_questions_pct", "Share of questions"),
                                    ("sql_success_pct", "SQL success %"),
                                    ("positive_feedback_pct", "Positive feedback %"),
                                    ("total_cost_usd", "Cost ($)"),
                                    ("agents_needing_attention", "Agents needing attention")],
               "Workspace leaderboard", width=4, height=5),
         pie("e_health", AG, "health_status", "count", "agent_id", "Agent health", HEALTH_COLORS, height=5)],
        [counter("e_projected", E, "projected_cost_after_free_usd", "Projected cost after free period", USD,
                 "Same usage with free Genie usage billed at list price"),
         counter("e_free", E, "free_usage_value_usd", "Free usage value", USD),
         counter("e_new_users", E, "new_users", "New users", INT),
         counter("e_attention", E, "agents_needing_attention", "Agents needing attention", INT,
                 "Low SQL success, low feedback, or usage down by half"),
         counter("e_api", E, "api_share_pct", "Questions via API / MCP", PCT),
         counter("e_agent_mode", E, "agent_mode_share_pct", "Questions in agent mode", PCT)],
    ])


def workspace_page(default=None):
    ds = [WS, AG, DA, C, U, Q]
    return page("workspace", "Workspace Deep Dive", [
        [text("ws_title", ["## Workspace deep dive",
                           "Pick a workspace to see its adoption, agents, users and cost (last 30 days)."],
              width=4, height=2),
         filter_widget("ws_filter", "filter-single-select", "workspace_name", "Workspace", ds, default=default)],
        [counter("w_users", WS, "active_users", "Active users", INT),
         counter("w_q", WS, "questions", "Questions", INT),
         counter("w_agents", WS, "active_agents", "Active agents", INT),
         counter("w_cost", WS, "total_cost_usd", "Total cost", USD),
         counter("w_cpu", WS, "cost_per_active_user", "Cost per active user", USD),
         counter("w_fb", WS, "positive_feedback_rate", "Positive feedback", PCT)],
        [counter("w_users_chg", WS, "active_users_change_pct", "Users vs prior 30d", PCT),
         counter("w_q_chg", WS, "questions_change_pct", "Questions vs prior 30d", PCT),
         counter("w_new", WS, "new_users", "New users", INT),
         counter("w_sql", WS, "sql_success_rate", "SQL success rate", PCT),
         counter("w_share", WS, "share_of_questions_pct", "Share of account questions", PCT),
         counter("w_attention", WS, "agents_needing_attention", "Agents needing attention", INT)],
        [chart("w_dau", "line", DA, time_bucket("DAY", "event_date"),
               [(agg("countdistinct", "user_email"), "Daily active users")], "Daily active users",
               x_title="Date", y_fmt=INT),
         chart("w_q_channel", "bar", DA, time_bucket("WEEK", "event_date"),
               [(agg("sum", "questions"), "Questions")], "Weekly questions by channel",
               color=col("channel"), x_title="Week", stacked=True, y_fmt=INT)],
        [table("w_agents_tbl", AG, [("agent_label", "Agent"), ("active_users", "Users"),
                                    ("questions", "Questions"), ("questions_change_pct", "Change vs prior"),
                                    ("sql_success_pct", "SQL success %"),
                                    ("positive_feedback_pct", "Positive feedback %"),
                                    ("p50_response_s", "Median response (s)"), ("total_cost_usd", "Cost ($)"),
                                    ("creator", "Creator"), ("health_status", "Health"),
                                    ("health_reason", "Why")],
               "Agents in this workspace", height=6)],
        [table("w_users_tbl", U, [("user_email", "User"), ("user_segment", "Segment"),
                                  ("questions_30d", "Questions"), ("active_days_30d", "Active days"),
                                  ("agents_used_30d", "Agents used"), ("top_agent", "Top agent"),
                                  ("last_seen", "Last seen")],
               "Users", width=4, height=6),
         pie("w_segments", U, "user_segment", "countdistinct", "user_email", "User segments", height=6)],
        [chart("w_cost_agent", "bar", C, col("agent_label"), [(agg("sum", "total_cost_usd"), "Cost")],
               "Cost by agent (all time in view)", x_type="categorical", y_fmt=USD, x_title="Agent"),
         chart("w_cost_daily", "bar", C, time_bucket("WEEK", "event_date"),
               [(agg("sum", "billable_cost_usd"), "Genie billable"),
                (agg("sum", "sql_compute_cost_usd"), "SQL compute (est.)")],
               "Weekly cost", x_title="Week", stacked=True)],
    ])


def agent_page(default=None):
    ds = [AG, DA, Q, C, H]
    return page("agent", "Agent Deep Dive", [
        [text("ag_title", ["## Agent deep dive",
                           "Pick a Genie agent to see who uses it, how well it answers, and what it costs."],
              width=4, height=2),
         filter_widget("ag_filter", "filter-single-select", "agent_label", "Genie agent", ds, default=default)],
        [counter("a_users", AG, "active_users", "Active users", INT),
         counter("a_q", AG, "questions", "Questions", INT),
         counter("a_conv", AG, "conversations", "Conversations", INT),
         counter("a_depth", AG, "questions_per_conversation", "Questions per conversation", NUM,
                 "Follow-up depth"),
         counter("a_cost", AG, "total_cost_usd", "Total cost", USD),
         counter("a_cpq", AG, "cost_per_question", "Cost per question", USD)],
        [counter("a_sql", AG, "sql_success_rate", "SQL success rate", PCT),
         counter("a_empty", AG, "empty_result_rate", "Empty results", PCT, "Finished queries with 0 rows"),
         counter("a_p50", AG, "p50_response_s", "Median SQL time (s)", NUM),
         counter("a_p95", AG, "p95_response_s", "95th pct SQL time (s)", NUM),
         counter("a_fb", AG, "positive_feedback_rate", "Positive feedback", PCT),
         counter("a_curation", AG, "curation_events_30d", "Curation edits (30d)", INT,
                 "Instruction, example and data changes")],
        [table("a_details", AG, [("workspace_name", "Workspace"), ("creator", "Creator"),
                                 ("created_date", "Created"), ("last_used_date", "Last used"),
                                 ("last_edited_date", "Last edited"), ("top_user", "Top user"),
                                 ("benchmark_runs_30d", "Benchmark runs (30d)"), ("health_status", "Health"),
                                 ("health_reason", "Why")],
               "Agent profile", height=3)],
        [chart("a_daily", "line", DA, time_bucket("DAY", "event_date"),
               [(agg("sum", "questions"), "Questions"), (agg("countdistinct", "user_email"), "Users")],
               "Daily questions and users", x_title="Date"),
         chart("a_quality", "bar", Q, time_bucket("WEEK", "event_date"),
               [(agg("sum", "sql_succeeded"), "Succeeded"), (agg("sum", "sql_failed"), "Failed"),
                (agg("sum", "empty_results"), "Empty result")],
               "Weekly SQL outcomes", x_title="Week", stacked=True)],
        [chart("a_hour", "bar", H, col("hour_of_day"), [(agg("sum", "questions"), "Questions")],
               "When people ask (hour of day, UTC, last 90 days)", x_type="categorical", y_fmt=INT,
               x_title="Hour", height=4),
         chart("a_dow", "bar", H, col("day_name"), [(agg("sum", "questions"), "Questions")],
               "Questions by day of week (last 90 days)", x_type="categorical", y_fmt=INT,
               x_title="Day", height=4)],
        [table("a_top_users", DA, [("user_email", "User"), ("questions", "Questions"), ("event_date", "Active days")],
               "Top users of this agent", width=3, height=6,
               aggregated={"questions": "sum", "event_date": "countdistinct"}),
         chart("a_feedback", "bar", Q, time_bucket("WEEK", "event_date"),
               [(agg("sum", "thumbs_up"), "Positive"), (agg("sum", "thumbs_down"), "Negative")],
               "Weekly feedback", x_title="Week", stacked=True, height=6)],
    ])


def quality_page():
    return page("quality", "Quality & Trust", [
        [text("q_title", ["## Quality and trust",
                          "Is Genie giving answers people can rely on? SQL outcomes come from query history; "
                          "feedback is the latest rating per answer."], height=2)],
        [counter("q_sql", E, "sql_success_rate", "SQL success rate", PCT, width=2),
         counter("q_fb", E, "positive_feedback_rate", "Positive feedback", PCT, width=1),
         counter("q_fb_n", E, "feedback_count", "Answers rated", INT, width=1),
         counter("q_p50", E, "p50_response_s", "Median SQL time (s)", NUM, width=1),
         counter("q_attention", E, "agents_needing_attention", "Agents needing attention", INT, width=1)],
        [chart("q_outcomes", "bar", Q, time_bucket("WEEK", "event_date"),
               [(agg("sum", "sql_succeeded"), "Succeeded"), (agg("sum", "sql_failed"), "Failed"),
                (agg("sum", "sql_canceled"), "Canceled"), (agg("sum", "empty_results"), "Empty result")],
               "Weekly Genie SQL outcomes", x_title="Week", stacked=True, width=6)],
        [chart("q_feedback", "bar", Q, time_bucket("WEEK", "event_date"),
               [(agg("sum", "thumbs_up"), "Positive"), (agg("sum", "thumbs_down"), "Negative")],
               "Weekly feedback", x_title="Week", stacked=True),
         chart("q_comments", "bar", Q, time_bucket("WEEK", "event_date"),
               [(agg("sum", "comments"), "Comments")], "Weekly comments on answers", x_title="Week",
               y_fmt=INT, description="Users commenting on an answer, often to ask for a review")],
        [table("q_agents", AG, [("agent_label", "Agent"), ("workspace_name", "Workspace"),
                                ("questions", "Questions"), ("sql_success_pct", "SQL success %"),
                                ("empty_result_pct", "Empty result %"),
                                ("positive_feedback_pct", "Positive feedback %"),
                                ("thumbs_down", "Negative ratings"), ("comments", "Comments"),
                                ("p95_response_s", "95th pct SQL time (s)"), ("health_status", "Health"),
                                ("health_reason", "Why")],
               "Agent quality scorecard (last 30 days)", height=7)],
    ])


def cost_page():
    return page("cost", "Cost & Chargeback", [
        [text("c_title", ["## Cost and chargeback",
                          "Genie pay-as-you-go cost plus the SQL warehouse compute Genie answers use, at list "
                          "price. Use the date range for chargeback periods."], width=4, height=2),
         date_filter("c_dates", "event_date", "Date range", [C])],
        [counter("c_total", E, "total_cost_usd", "Total cost (30d)", USD),
         counter("c_billable", E, "billable_cost_usd", "Genie billable (30d)", USD),
         counter("c_compute", E, "sql_compute_cost_usd", "SQL compute est. (30d)", USD),
         counter("c_free", E, "free_usage_value_usd", "Free usage value (30d)", USD),
         counter("c_projected", E, "projected_cost_after_free_usd", "Projected after free period", USD),
         counter("c_cpq", E, "cost_per_question", "Cost per question", USD)],
        [chart("c_monthly", "bar", M, MONTH,
               [(col("billable_cost_usd"), "Genie billable"), (col("sql_compute_cost_usd"), "SQL compute (est.)"),
                (col("free_usage_value_usd"), "Free usage value")],
               "Monthly cost", x_title="Month", stacked=True),
         chart("c_by_ws", "bar", C, time_bucket("WEEK", "event_date"), [(agg("sum", "total_cost_usd"), "Cost")],
               "Weekly cost by workspace", color=col("workspace_name"), x_title="Week", stacked=True, y_fmt=USD)],
        [table("c_chargeback", C, [("workspace_name", "Workspace"), ("billable_cost_usd", "Genie billable ($)"),
                                   ("sql_compute_cost_usd", "SQL compute est. ($)"),
                                   ("total_cost_usd", "Total ($)"), ("free_usage_value_usd", "Free usage value ($)"),
                                   ("genie_dbus", "Genie DBUs")],
               "Chargeback by workspace (selected dates)", width=3, height=6,
               aggregated={"billable_cost_usd": "sum", "sql_compute_cost_usd": "sum", "total_cost_usd": "sum",
                           "free_usage_value_usd": "sum", "genie_dbus": "sum"}),
         table("c_agents", C, [("agent_label", "Agent"), ("workspace_name", "Workspace"),
                               ("total_cost_usd", "Total ($)"), ("free_usage_value_usd", "Free usage value ($)")],
               "Cost by agent (selected dates)", width=3, height=6,
               aggregated={"total_cost_usd": "sum", "free_usage_value_usd": "sum"})],
        [table("c_users", C, [("user_email", "User"), ("workspace_name", "Workspace"),
                              ("billable_cost_usd", "Genie billable ($)"),
                              ("free_usage_value_usd", "Free usage value ($)")],
               "Cost by user (selected dates)", width=4, height=6,
               aggregated={"billable_cost_usd": "sum", "free_usage_value_usd": "sum"}),
         pie("c_channel", C, "channel", "sum", "total_cost_usd", "Cost by channel", height=6)],
    ])


def users_page():
    return page("users", "Users & Retention", [
        [text("u_title", ["## Users and retention",
                          "Who uses Genie, how often, and whether they come back."], height=2)],
        [counter("u_active", E, "active_users", "Active users (30d)", INT),
         counter("u_new", E, "new_users", "New users (30d)", INT),
         counter("u_ret", E, "user_retention_rate", "User retention", PCT),
         counter("u_sticky", E, "stickiness_dau_mau", "Stickiness (DAU/MAU)", PCT),
         counter("u_qpu", E, "questions_per_user", "Questions per user", NUM),
         counter("u_all", E, "users_all_time", "Users all time (13 mo)", INT)],
        [chart("u_monthly", "bar", M, MONTH, [(col("active_users"), "Active users"), (col("new_users"), "New users")],
               "Active and new users by month", x_title="Month", width=4),
         pie("u_segments", U, "user_segment", "countdistinct", "user_email", "User segments (30d)")],
        [chart("u_cohorts", "line", R, col("months_since_first"), [(agg("avg", "retention_rate"), "Retention")],
               "Retention by monthly cohort", color=col("cohort_month"), x_type="quantitative", y_fmt=PCT,
               x_title="Months since first question", width=6,
               description="Share of each month's new users still asking questions N months later")],
        [table("u_power", U, [("user_email", "User"), ("workspace_name", "Workspace"),
                              ("user_segment", "Segment"), ("questions_30d", "Questions (30d)"),
                              ("active_days_30d", "Active days"), ("agents_used_30d", "Agents used"),
                              ("top_agent", "Top agent"), ("first_seen", "First seen"),
                              ("last_seen", "Last seen")],
               "User directory (sorted by questions)", height=7)],
    ])


def governance_page():
    return page("governance", "Governance & Hygiene", [
        [text("g_title", ["## Governance and hygiene",
                          "Keep the agent catalog clean: find stale, unused and struggling agents, and see "
                          "who builds and maintains them."], width=2, height=2),
         filter_widget("g_health", "filter-multi-select", "health_status", "Health status", [AG]),
         filter_widget("g_ws", "filter-multi-select", "workspace_name", "Workspace", [AG])],
        [counter("g_total", E, "agents_total", "Agents (not trashed)", INT),
         counter("g_active", E, "active_agents", "Active agents (30d)", INT),
         counter("g_created", E, "agents_created", "Created (30d)", INT),
         counter("g_attention", E, "agents_needing_attention", "Needing attention", INT),
         counter_agg("g_curation", AG, "sum", "curation_events_30d", "Curation edits (30d)", INT),
         counter_agg("g_bench", AG, "sum", "benchmark_runs_30d", "Benchmark runs (30d)", INT)],
        [chart("g_by_ws", "bar", AG, col("workspace_name"), [(agg("count", "agent_id"), "Agents")],
               "Agents by workspace and health", color=col("health_status"), x_type="categorical",
               stacked=True, color_mappings=HEALTH_COLORS, x_title="Workspace", y_fmt=INT),
         chart("g_creators", "bar", AG, col("creator"), [(agg("count", "agent_id"), "Agents")],
               "Agents by creator", x_type="categorical", x_title="Creator", y_fmt=INT)],
        [table("g_agents", AG, [("agent_label", "Agent"), ("workspace_name", "Workspace"), ("creator", "Creator"),
                                ("created_date", "Created"), ("last_used_date", "Last used"),
                                ("days_since_last_use", "Days idle"), ("last_edited_date", "Last edited"),
                                ("active_users", "Users (30d)"), ("questions", "Questions (30d)"),
                                ("curation_events_30d", "Edits (30d)"), ("benchmark_runs_30d", "Benchmarks (30d)"),
                                ("health_status", "Health"), ("health_reason", "Why")],
               "Agent inventory", height=8)],
    ])


def about_page(system_only):
    names = ("Agent names come from the latest name in the audit log (system-tables-only build)."
             if system_only else
             "Agent names come from the `genie_agent_dim` table (refreshed by notebooks/refresh_agent_dim.py), "
             "falling back to the audit log.")
    return page("about", "About & Definitions", [
        [text("about_txt", [
            "## About Genie Watchtower",
            "**Sources:** system.access.audit (Genie events), system.billing.usage and list_prices "
            "(Genie pay-as-you-go and SQL warehouse cost), system.query.history (Genie-generated SQL), "
            "system.access.workspaces_latest.",
            f"**Agent names:** {names}",
            "**Windows:** KPIs use the last 30 complete days; trends cover 13 months. Costs are list "
            "price; negotiated discounts are not reflected. Questions are counted for the workspaces "
            "in this metastore's region that the system tables cover.",
        ], height=4)],
        [table("about_defs", "ds_definitions", [("kpi", "KPI"), ("definition", "Definition")],
               "KPI definitions", height=10)],
    ])


def build_dashboard(system_only=False, workspace_default=None, agent_default=None):
    datasets = build_datasets(with_dim=not system_only)
    return {
        "datasets": [{"name": d.name, "displayName": d.display, "queryLines": [d.sql]} for d in datasets],
        "pages": [exec_page(), workspace_page(workspace_default), agent_page(agent_default), quality_page(),
                  cost_page(), users_page(), governance_page(), about_page(system_only)],
        "uiSettings": {"theme": {"widgetHeaderAlignment": "LEFT"}},
    }


def single_select_default(value):
    return {"defaultSelection": {"values": {"dataType": "STRING", "values": [{"value": value}]}}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--system-only", action="store_true")
    a = p.parse_args()
    out = ROOT / ("dashboard_system_only.json" if a.system_only else "dashboard.json")
    out.write_text(json.dumps(build_dashboard(system_only=a.system_only), indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
