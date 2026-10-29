"""Dataset definitions for Genie Watchtower v2.

Every dataset reads only Databricks system tables (system.access, system.billing,
system.query), plus an optional agent-name lookup table (genie_agent_dim) written by
notebooks/refresh_agent_dim.py. Build with AGENT_DIM=None for a system-tables-only
dashboard that falls back to agent names found in the audit log.

Time windows are fixed and computed in SQL so ratio KPIs are exact:
  current window = the last 30 complete days  [today-30, today)
  prior window   = the 30 days before that     [today-60, today-30)
Trends cover the last 13 months.
"""
from dataclasses import dataclass, field

# Optional lookup table with agent titles. Unqualified so the dashboard's dataset
# catalog/schema (set at deploy time) decides where it lives.
AGENT_DIM = "genie_agent_dim"

QUESTION_ACTIONS = (
    "createConversationMessage", "createAgenticResponse",            # UI
    "genieStartConversationMessage", "genieCreateConversationMessage",  # API / MCP
    "genieCreateAgenticResponse",
)
CONVERSATION_ACTIONS = ("createConversation", "genieStartConversationMessage")
CURATION_ACTIONS = (
    "createInstruction", "updateInstruction", "deleteInstruction",
    "createCuratedQuestion", "updateCuratedQuestion", "deleteCuratedQuestion",
    "updateGenieColumnConfigs", "addDataSources", "removeDataSources",
    "updateSpace", "genieUpdateSpace", "updateSampleQuestions",
)
BENCHMARK_ACTIONS = ("createEvaluationRun", "genieCreateEvalRun")
CREATE_ACTIONS = ("createSpace", "genieCreateSpace", "cloneSpace")
TRASH_ACTIONS = ("trashSpace", "genieTrashSpace")


def _in(values):
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


CUR = "event_date >= date_sub(current_date(), 30) AND event_date < current_date()"
PRIOR = ("event_date >= date_sub(current_date(), 60) "
         "AND event_date < date_sub(current_date(), 30)")
HISTORY = "add_months(trunc(current_date(), 'MM'), -12)"


# ---------------------------------------------------------------- shared CTEs
def cte_workspaces():
    return """workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
)"""


def cte_questions():
    return f"""questions AS (
  SELECT
    a.event_date,
    a.event_time,
    CAST(a.workspace_id AS STRING) AS workspace_id,
    a.request_params['space_id'] AS agent_id,
    lower(a.user_identity.email) AS user_email,
    CASE WHEN a.action_name LIKE 'genie%' THEN 'API' ELSE 'UI' END AS channel,
    CASE WHEN a.action_name ILIKE '%agentic%' THEN 'Agent mode' ELSE 'Standard' END AS mode
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie'
    AND a.action_name IN {_in(QUESTION_ACTIONS)}
    AND a.response.status_code = 200
    AND a.request_params['space_id'] IS NOT NULL
    AND a.event_date >= {HISTORY}
)"""


def cte_conversations():
    return f"""conversations AS (
  SELECT a.event_date, a.request_params['space_id'] AS agent_id, lower(a.user_identity.email) AS user_email
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie'
    AND a.action_name IN {_in(CONVERSATION_ACTIONS)}
    AND a.response.status_code = 200
    AND a.request_params['space_id'] IS NOT NULL
    AND a.event_date >= {HISTORY}
)"""


def cte_feedback():
    # Users can change a rating; keep the latest rating per message.
    return f"""feedback AS (
  SELECT event_date, agent_id, user_email,
         CASE WHEN rating IN ('THUMBS_UP', 'POSITIVE') THEN 1 ELSE 0 END AS is_positive,
         CASE WHEN rating IN ('THUMBS_DOWN', 'NEGATIVE') THEN 1 ELSE 0 END AS is_negative
  FROM (
    SELECT a.event_date, a.request_params['space_id'] AS agent_id,
           lower(a.user_identity.email) AS user_email,
           coalesce(a.request_params['feedback_rating'], a.request_params['rating']) AS rating,
           row_number() OVER (PARTITION BY a.request_params['message_id'] ORDER BY a.event_time DESC) AS rn
    FROM system.access.audit a
    WHERE a.service_name = 'aibiGenie'
      AND a.action_name IN ('updateConversationMessageFeedback', 'genieSendMessageFeedback')
      AND a.request_params['space_id'] IS NOT NULL
      AND a.event_date >= {HISTORY}
  )
  WHERE rn = 1 AND rating IN ('THUMBS_UP', 'POSITIVE', 'THUMBS_DOWN', 'NEGATIVE')
)"""


def cte_comments():
    return f"""comments AS (
  SELECT a.event_date, a.request_params['space_id'] AS agent_id
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie' AND a.action_name = 'createConversationMessageComment'
    AND a.request_params['space_id'] IS NOT NULL AND a.event_date >= {HISTORY}
)"""


def cte_curation():
    return f"""curation AS (
  SELECT a.event_date, a.request_params['space_id'] AS agent_id, lower(a.user_identity.email) AS user_email,
         CASE WHEN a.action_name IN {_in(BENCHMARK_ACTIONS)} THEN 1 ELSE 0 END AS is_benchmark
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie'
    AND a.action_name IN {_in(CURATION_ACTIONS + BENCHMARK_ACTIONS)}
    AND a.response.status_code = 200
    AND a.request_params['space_id'] IS NOT NULL
)"""


def cte_genie_sql():
    return f"""genie_sql AS (
  SELECT DATE(start_time) AS event_date,
         CAST(workspace_id AS STRING) AS workspace_id,
         query_source.genie_space_id AS agent_id,
         lower(executed_by) AS user_email,
         execution_status,
         total_duration_ms,
         produced_rows
  FROM system.query.history
  WHERE query_source.genie_space_id IS NOT NULL
    AND start_time >= {HISTORY}
)"""


def cte_prices():
    return """prices AS (
  SELECT sku_name, cloud, price_start_time, price_end_time,
         CAST(pricing.default AS DOUBLE) AS unit_price
  FROM system.billing.list_prices
),
free_rate AS (
  -- Free Genie usage has no list price. Value it at the list price of the billable
  -- Genie SKU this account actually uses (falls back to any serverless inference SKU).
  SELECT coalesce(
    (SELECT max_by(p.unit_price, cnt) FROM (
        SELECT u.sku_name, u.cloud, count(*) AS cnt FROM system.billing.usage u
        WHERE u.billing_origin_product = 'GENIE' AND u.sku_name <> 'GENIE_FREE_USAGE'
        GROUP BY ALL) s
     JOIN prices p ON p.sku_name = s.sku_name AND p.cloud = s.cloud AND p.price_end_time IS NULL),
    (SELECT min(unit_price) FROM prices
     WHERE sku_name LIKE '%SERVERLESS_REAL_TIME_INFERENCE%' AND price_end_time IS NULL)
  ) AS rate
)"""


def cte_genie_billing():
    # Includes RETRACTION / RESTATEMENT rows so corrections net out when summed.
    return f"""genie_billing AS (
  SELECT u.usage_date AS event_date,
         CAST(u.workspace_id AS STRING) AS workspace_id,
         u.usage_metadata.genie.agent_id AS agent_id,
         lower(u.identity_metadata.run_as) AS user_email,
         coalesce(u.usage_metadata.genie.channel, 'Unknown') AS channel,
         u.usage_quantity AS dbus,
         CASE WHEN u.sku_name = 'GENIE_FREE_USAGE' THEN 0
              ELSE u.usage_quantity * coalesce(p.unit_price, 0) END AS billable_cost_usd,
         CASE WHEN u.sku_name = 'GENIE_FREE_USAGE' THEN u.usage_quantity * fr.rate
              ELSE 0 END AS free_usage_value_usd
  FROM system.billing.usage u
  CROSS JOIN free_rate fr
  LEFT JOIN prices p
    ON u.sku_name = p.sku_name AND u.cloud = p.cloud
   AND u.usage_start_time >= p.price_start_time
   AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.billing_origin_product = 'GENIE'
    AND u.usage_date >= {HISTORY}
)"""


def cte_sql_compute():
    # Genie answers run SQL on the agent's warehouse. Attribute each warehouse's
    # daily list-price cost to agents by their share of that warehouse's query time.
    return f"""wh_cost AS (
  SELECT u.usage_date AS event_date, u.usage_metadata.warehouse_id AS warehouse_id,
         SUM(u.usage_quantity * coalesce(p.unit_price, 0)) AS cost
  FROM system.billing.usage u
  LEFT JOIN prices p
    ON u.sku_name = p.sku_name AND u.cloud = p.cloud
   AND u.usage_start_time >= p.price_start_time
   AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_metadata.warehouse_id IS NOT NULL AND u.usage_date >= {HISTORY}
  GROUP BY ALL
),
wh_time AS (
  SELECT DATE(start_time) AS event_date, compute.warehouse_id AS warehouse_id,
         CAST(workspace_id AS STRING) AS workspace_id,
         query_source.genie_space_id AS agent_id, lower(executed_by) AS user_email,
         SUM(total_duration_ms) AS ms
  FROM system.query.history
  WHERE compute.warehouse_id IS NOT NULL AND start_time >= {HISTORY}
  GROUP BY ALL
),
sql_compute AS (
  SELECT t.event_date, t.workspace_id, t.agent_id, t.user_email,
         c.cost * t.ms / NULLIF(SUM(t.ms) OVER (PARTITION BY t.event_date, t.warehouse_id), 0)
           AS sql_compute_cost_usd
  FROM wh_time t JOIN wh_cost c ON c.event_date = t.event_date AND c.warehouse_id = t.warehouse_id
),
genie_sql_compute AS (
  SELECT * FROM sql_compute WHERE agent_id IS NOT NULL
)"""


def cte_agents(with_dim):
    dim_name = (f"""dim_names AS (
  SELECT agent_id, title AS dim_title, CAST(workspace_id AS STRING) AS dim_workspace_id
  FROM {AGENT_DIM}
),""" if with_dim else "")
    dim_join = "LEFT JOIN dim_names d ON d.agent_id = u.agent_id" if with_dim else ""
    name_expr = "coalesce(d.dim_title, n.audit_name)" if with_dim else "n.audit_name"
    ws_expr = ("coalesce(c.created_workspace_id, d.dim_workspace_id, q.last_workspace_id)"
               if with_dim else "coalesce(c.created_workspace_id, q.last_workspace_id)")
    dim_union = "UNION SELECT agent_id FROM dim_names" if with_dim else ""
    return f"""{dim_name}
audit_names AS (
  SELECT agent_id, audit_name FROM (
    SELECT a.request_params['space_id'] AS agent_id, a.request_params['display_name'] AS audit_name,
           row_number() OVER (PARTITION BY a.request_params['space_id'] ORDER BY a.event_time DESC) AS rn
    FROM system.access.audit a
    WHERE a.service_name = 'aibiGenie' AND a.action_name = 'updateSpace'
      AND coalesce(a.request_params['display_name'], '') <> ''
  ) WHERE rn = 1
),
created AS (
  SELECT get_json_object(a.response.result, '$.space_id') AS agent_id,
         min(a.event_date) AS created_date,
         min_by(lower(a.user_identity.email), a.event_time) AS creator,
         min_by(CAST(a.workspace_id AS STRING), a.event_time) AS created_workspace_id
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie' AND a.action_name IN {_in(CREATE_ACTIONS)}
    AND a.response.status_code = 200
  GROUP BY 1
),
trashed AS (
  SELECT a.request_params['space_id'] AS agent_id, max(a.event_date) AS trashed_date
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie' AND a.action_name IN {_in(TRASH_ACTIONS)}
    AND a.response.status_code = 200
  GROUP BY 1
),
q_last AS (
  SELECT agent_id, max(event_date) AS last_used_date,
         max_by(workspace_id, event_time) AS last_workspace_id
  FROM questions GROUP BY 1
),
agent_universe AS (
  SELECT agent_id FROM questions
  UNION SELECT agent_id FROM created WHERE created_date >= {HISTORY}
  {dim_union}
),
agents AS (
  SELECT u.agent_id,
         {name_expr} AS agent_name,
         {ws_expr} AS workspace_id,
         c.creator, c.created_date, t.trashed_date, q.last_used_date
  FROM agent_universe u
  LEFT JOIN audit_names n ON n.agent_id = u.agent_id
  {dim_join}
  LEFT JOIN created c ON c.agent_id = u.agent_id
  LEFT JOIN trashed t ON t.agent_id = u.agent_id
  LEFT JOIN q_last q ON q.agent_id = u.agent_id
  WHERE u.agent_id IS NOT NULL
),
agent_labels AS (
  SELECT a.*,
         coalesce(w.workspace_name, a.workspace_id, 'Unknown workspace') AS workspace_name,
         CASE WHEN a.agent_name IS NULL
                THEN concat('Unnamed agent ', right(a.agent_id, 6))
              WHEN count(*) OVER (PARTITION BY lower(a.agent_name)) > 1
                THEN concat(a.agent_name, ' (', right(a.agent_id, 6), ')')
              ELSE a.agent_name END AS agent_label
  FROM agents a LEFT JOIN workspaces w ON w.workspace_id = a.workspace_id
)"""


def with_ctes(*ctes):
    return "WITH " + ",\n".join(c.strip().rstrip(",") for c in ctes if c.strip())


def agent_ctes(with_dim):
    return (cte_workspaces(), cte_questions(), cte_agents(with_dim))


# ---------------------------------------------------------------- datasets
@dataclass(frozen=True)
class Dataset:
    name: str
    display: str
    columns: tuple
    sql_builder: object = field(repr=False)
    may_be_empty: bool = False
    with_dim: bool = True

    @property
    def sql(self):
        return self.sql_builder(self.with_dim)


def _exec_kpis(with_dim):
    return with_ctes(
        *agent_ctes(with_dim), cte_feedback(), cte_genie_sql(), cte_prices(),
        cte_genie_billing(), cte_sql_compute()) + f"""
, cur_q AS (SELECT * FROM questions WHERE {CUR}),
prior_q AS (SELECT * FROM questions WHERE {PRIOR}),
first_seen AS (SELECT user_email, min(event_date) AS first_date FROM questions GROUP BY 1),
daily_users AS (SELECT event_date, count(DISTINCT user_email) AS dau FROM cur_q GROUP BY 1),
health AS (SELECT count_if(health_status IN ('Needs attention', 'Declining')) AS n FROM ({_agent_kpis_body()}))
SELECT
  date_sub(current_date(), 1) AS as_of,
  (SELECT count(DISTINCT user_email) FROM cur_q) AS active_users,
  (SELECT count(DISTINCT user_email) FROM prior_q) AS active_users_prior,
  try_divide((SELECT count(DISTINCT user_email) FROM cur_q) - (SELECT count(DISTINCT user_email) FROM prior_q),
             (SELECT count(DISTINCT user_email) FROM prior_q)) AS active_users_change_pct,
  (SELECT count(*) FROM cur_q) AS questions,
  (SELECT count(*) FROM prior_q) AS questions_prior,
  try_divide((SELECT count(*) FROM cur_q) - (SELECT count(*) FROM prior_q), (SELECT count(*) FROM prior_q))
    AS questions_change_pct,
  (SELECT count(DISTINCT agent_id) FROM cur_q) AS active_agents,
  (SELECT count(DISTINCT agent_id) FROM prior_q) AS active_agents_prior,
  (SELECT count(*) FROM first_seen WHERE first_date >= date_sub(current_date(), 30)
     AND first_date < current_date()) AS new_users,
  (SELECT count(DISTINCT user_email) FROM questions) AS users_all_time,
  try_divide((SELECT count(*) FROM cur_q), (SELECT count(DISTINCT user_email) FROM cur_q)) AS questions_per_user,
  try_divide((SELECT avg(dau) FROM daily_users), (SELECT count(DISTINCT user_email) FROM cur_q))
    AS stickiness_dau_mau,
  try_divide((SELECT count(DISTINCT p.user_email) FROM prior_q p
              WHERE p.user_email IN (SELECT user_email FROM cur_q)),
             (SELECT count(DISTINCT user_email) FROM prior_q)) AS user_retention_rate,
  (SELECT count(DISTINCT workspace_id) FROM cur_q) AS workspaces_with_genie,
  (SELECT count(*) FROM workspaces WHERE status = 'RUNNING' OR status IS NULL) AS workspaces_total,
  try_divide((SELECT count(DISTINCT workspace_id) FROM cur_q),
             (SELECT count(*) FROM workspaces WHERE status = 'RUNNING' OR status IS NULL))
    AS workspace_coverage_pct,
  (SELECT count(*) FROM agent_labels WHERE created_date >= date_sub(current_date(), 30)) AS agents_created,
  (SELECT count(*) FROM agent_labels WHERE trashed_date IS NULL) AS agents_total,
  (SELECT try_divide(count_if(execution_status = 'FINISHED'),
                     count_if(execution_status IN ('FINISHED', 'FAILED')))
     FROM genie_sql WHERE {CUR}) AS sql_success_rate,
  (SELECT try_divide(sum(is_positive), sum(is_positive) + sum(is_negative)) FROM feedback WHERE {CUR})
    AS positive_feedback_rate,
  (SELECT count(*) FROM feedback WHERE {CUR}) AS feedback_count,
  (SELECT round(percentile(total_duration_ms, 0.5) / 1000, 1) FROM genie_sql
     WHERE {CUR} AND execution_status = 'FINISHED') AS p50_response_s,
  (SELECT round(coalesce(sum(billable_cost_usd), 0), 2) FROM genie_billing WHERE {CUR}) AS billable_cost_usd,
  (SELECT round(coalesce(sum(free_usage_value_usd), 0), 2) FROM genie_billing WHERE {CUR})
    AS free_usage_value_usd,
  (SELECT round(coalesce(sum(sql_compute_cost_usd), 0), 2) FROM genie_sql_compute WHERE {CUR})
    AS sql_compute_cost_usd,
  round((SELECT coalesce(sum(billable_cost_usd), 0) FROM genie_billing WHERE {CUR})
      + (SELECT coalesce(sum(sql_compute_cost_usd), 0) FROM genie_sql_compute WHERE {CUR}), 2) AS total_cost_usd,
  round(try_divide((SELECT coalesce(sum(billable_cost_usd), 0) FROM genie_billing WHERE {CUR})
      + (SELECT coalesce(sum(sql_compute_cost_usd), 0) FROM genie_sql_compute WHERE {CUR}),
      (SELECT count(DISTINCT user_email) FROM cur_q)), 2) AS cost_per_active_user,
  round(try_divide((SELECT coalesce(sum(billable_cost_usd), 0) FROM genie_billing WHERE {CUR})
      + (SELECT coalesce(sum(sql_compute_cost_usd), 0) FROM genie_sql_compute WHERE {CUR}),
      (SELECT count(*) FROM cur_q)), 3) AS cost_per_question,
  round((SELECT coalesce(sum(billable_cost_usd) + sum(free_usage_value_usd), 0) FROM genie_billing WHERE {CUR})
      + (SELECT coalesce(sum(sql_compute_cost_usd), 0) FROM genie_sql_compute WHERE {CUR}), 2)
    AS projected_cost_after_free_usd,
  try_divide((SELECT count_if(channel = 'API') FROM cur_q), (SELECT count(*) FROM cur_q)) AS api_share_pct,
  try_divide((SELECT count_if(mode = 'Agent mode') FROM cur_q), (SELECT count(*) FROM cur_q))
    AS agent_mode_share_pct,
  (SELECT n FROM health) AS agents_needing_attention"""


EXEC_COLUMNS = (
    "as_of", "active_users", "active_users_prior", "active_users_change_pct", "questions",
    "questions_prior", "questions_change_pct", "active_agents", "active_agents_prior", "new_users",
    "users_all_time", "questions_per_user", "stickiness_dau_mau", "user_retention_rate",
    "workspaces_with_genie", "workspaces_total", "workspace_coverage_pct", "agents_created",
    "agents_total", "sql_success_rate", "positive_feedback_rate", "feedback_count", "p50_response_s",
    "billable_cost_usd", "free_usage_value_usd", "sql_compute_cost_usd", "total_cost_usd",
    "cost_per_active_user", "cost_per_question", "projected_cost_after_free_usd", "api_share_pct",
    "agent_mode_share_pct", "agents_needing_attention",
)


def _agent_kpis_body():
    """Per-agent KPIs. Expects the agent, feedback, comments, curation, genie_sql,
    genie_billing and genie_sql_compute CTEs to be in scope."""
    return f"""
SELECT
  a.agent_id, a.agent_label, a.agent_name, a.workspace_name, a.creator, a.created_date,
  a.last_used_date,
  datediff(current_date(), a.last_used_date) AS days_since_last_use,
  ed.last_edited_date,
  coalesce(q.active_users, 0) AS active_users,
  coalesce(qp.active_users_prior, 0) AS active_users_prior,
  coalesce(q.questions, 0) AS questions,
  coalesce(qp.questions_prior, 0) AS questions_prior,
  try_divide(coalesce(q.questions, 0) - qp.questions_prior, qp.questions_prior) AS questions_change_pct,
  coalesce(cv.conversations, 0) AS conversations,
  try_divide(q.questions, cv.conversations) AS questions_per_conversation,
  try_divide(q.api_questions, q.questions) AS api_share_pct,
  coalesce(s.sql_queries, 0) AS sql_queries,
  s.sql_success_rate, s.empty_result_rate, s.p50_response_s, s.p95_response_s,
  coalesce(f.thumbs_up, 0) AS thumbs_up,
  coalesce(f.thumbs_down, 0) AS thumbs_down,
  try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) AS positive_feedback_rate,
  coalesce(cm.comments, 0) AS comments,
  coalesce(cu.curation_events, 0) AS curation_events_30d,
  coalesce(cu.benchmark_runs, 0) AS benchmark_runs_30d,
  round(coalesce(b.billable_cost_usd, 0), 2) AS billable_cost_usd,
  round(coalesce(b.free_usage_value_usd, 0), 2) AS free_usage_value_usd,
  round(coalesce(sc.sql_compute_cost_usd, 0), 2) AS sql_compute_cost_usd,
  round(coalesce(b.billable_cost_usd, 0) + coalesce(sc.sql_compute_cost_usd, 0), 2) AS total_cost_usd,
  round(try_divide(coalesce(b.billable_cost_usd, 0) + coalesce(sc.sql_compute_cost_usd, 0), q.questions), 3)
    AS cost_per_question,
  q.top_user,
  CASE
    WHEN a.trashed_date IS NOT NULL THEN 'Trashed'
    WHEN a.last_used_date IS NULL THEN 'Unused'
    WHEN coalesce(q.questions, 0) = 0 THEN 'Stale'
    WHEN q.questions >= 10 AND (s.sql_success_rate < 0.9
         OR (f.thumbs_up + f.thumbs_down >= 5 AND try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) < 0.6))
      THEN 'Needs attention'
    WHEN qp.questions_prior >= 10 AND q.questions < 0.5 * qp.questions_prior THEN 'Declining'
    WHEN coalesce(qp.questions_prior, 0) = 0 OR q.questions >= 1.5 * qp.questions_prior THEN 'Growing'
    ELSE 'Healthy'
  END AS health_status,
  CASE
    WHEN a.trashed_date IS NOT NULL THEN concat('Trashed on ', a.trashed_date)
    WHEN a.last_used_date IS NULL THEN 'Created but never asked a question'
    WHEN coalesce(q.questions, 0) = 0 THEN concat('No questions in 30 days (last used ', a.last_used_date, ')')
    WHEN q.questions >= 10 AND s.sql_success_rate < 0.9
      THEN concat('SQL success ', round(s.sql_success_rate * 100), '%')
    WHEN q.questions >= 10 AND f.thumbs_up + f.thumbs_down >= 5
         AND try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) < 0.6
      THEN concat('Positive feedback ', round(try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) * 100), '%')
    WHEN qp.questions_prior >= 10 AND q.questions < 0.5 * qp.questions_prior
      THEN concat('Questions down from ', qp.questions_prior, ' to ', q.questions)
    ELSE ''
  END AS health_reason
FROM agent_labels a
LEFT JOIN (
  SELECT agent_id, count(*) AS questions, count(DISTINCT user_email) AS active_users,
         count_if(channel = 'API') AS api_questions,
         mode(user_email) AS top_user
  FROM questions WHERE {CUR} GROUP BY 1) q ON q.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, count(*) AS questions_prior, count(DISTINCT user_email) AS active_users_prior
  FROM questions WHERE {PRIOR} GROUP BY 1) qp ON qp.agent_id = a.agent_id
LEFT JOIN (SELECT agent_id, count(*) AS conversations FROM conversations WHERE {CUR} GROUP BY 1) cv
  ON cv.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, count(*) AS sql_queries,
         try_divide(count_if(execution_status = 'FINISHED'),
                    count_if(execution_status IN ('FINISHED', 'FAILED'))) AS sql_success_rate,
         try_divide(count_if(execution_status = 'FINISHED' AND produced_rows = 0),
                    count_if(execution_status = 'FINISHED')) AS empty_result_rate,
         round(percentile(CASE WHEN execution_status = 'FINISHED' THEN total_duration_ms END, 0.5) / 1000, 1)
           AS p50_response_s,
         round(percentile(CASE WHEN execution_status = 'FINISHED' THEN total_duration_ms END, 0.95) / 1000, 1)
           AS p95_response_s
  FROM genie_sql WHERE {CUR} GROUP BY 1) s ON s.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, sum(is_positive) AS thumbs_up, sum(is_negative) AS thumbs_down
  FROM feedback WHERE {CUR} GROUP BY 1) f ON f.agent_id = a.agent_id
LEFT JOIN (SELECT agent_id, count(*) AS comments FROM comments WHERE {CUR} GROUP BY 1) cm
  ON cm.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, count_if(is_benchmark = 0) AS curation_events, sum(is_benchmark) AS benchmark_runs
  FROM curation WHERE {CUR} GROUP BY 1) cu ON cu.agent_id = a.agent_id
LEFT JOIN (SELECT agent_id, max(event_date) AS last_edited_date FROM curation WHERE is_benchmark = 0 GROUP BY 1) ed
  ON ed.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, sum(billable_cost_usd) AS billable_cost_usd, sum(free_usage_value_usd) AS free_usage_value_usd
  FROM genie_billing WHERE {CUR} GROUP BY 1) b ON b.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, sum(sql_compute_cost_usd) AS sql_compute_cost_usd
  FROM genie_sql_compute WHERE {CUR} GROUP BY 1) sc ON sc.agent_id = a.agent_id
"""


def _all_agent_ctes(with_dim):
    return (*agent_ctes(with_dim), cte_conversations(), cte_feedback(), cte_comments(), cte_curation(),
            cte_genie_sql(), cte_prices(), cte_genie_billing(), cte_sql_compute())


def _exec_kpis_full(with_dim):
    # The exec dataset embeds the agent body for its health count, so it needs every agent CTE.
    sql = _exec_kpis(with_dim)
    head = with_ctes(*_all_agent_ctes(with_dim))
    return head + sql[sql.index("\n, cur_q AS"):]


def _agent_kpis(with_dim):
    return with_ctes(*_all_agent_ctes(with_dim)) + _agent_kpis_body()


AGENT_COLUMNS = (
    "agent_id", "agent_label", "agent_name", "workspace_name", "creator", "created_date",
    "last_used_date", "days_since_last_use", "last_edited_date", "active_users", "active_users_prior",
    "questions", "questions_prior", "questions_change_pct", "conversations",
    "questions_per_conversation", "api_share_pct", "sql_queries", "sql_success_rate",
    "empty_result_rate", "p50_response_s", "p95_response_s", "thumbs_up", "thumbs_down",
    "positive_feedback_rate", "comments", "curation_events_30d", "benchmark_runs_30d",
    "billable_cost_usd", "free_usage_value_usd", "sql_compute_cost_usd", "total_cost_usd",
    "cost_per_question", "top_user", "health_status", "health_reason",
)


def _workspace_kpis(with_dim):
    return with_ctes(*_all_agent_ctes(with_dim)) + f"""
, ag AS ({_agent_kpis_body()}),
ws_q AS (SELECT coalesce(w.workspace_name, q.workspace_id) AS workspace_name, q.*
         FROM questions q LEFT JOIN workspaces w ON w.workspace_id = q.workspace_id),
first_seen AS (SELECT user_email, workspace_name, min(event_date) AS first_date FROM ws_q GROUP BY 1, 2),
ws_cost AS (
  SELECT coalesce(w.workspace_name, b.workspace_id) AS workspace_name,
         sum(billable_cost_usd) AS billable, sum(free_usage_value_usd) AS free_value
  FROM genie_billing b LEFT JOIN workspaces w ON w.workspace_id = b.workspace_id
  WHERE {CUR.replace('event_date', 'b.event_date')} GROUP BY 1),
ws_compute AS (
  SELECT coalesce(w.workspace_name, c.workspace_id) AS workspace_name, sum(sql_compute_cost_usd) AS compute
  FROM genie_sql_compute c LEFT JOIN workspaces w ON w.workspace_id = c.workspace_id
  WHERE {CUR.replace('event_date', 'c.event_date')} GROUP BY 1),
ws_sql AS (
  SELECT coalesce(w.workspace_name, s.workspace_id) AS workspace_name,
         try_divide(count_if(execution_status = 'FINISHED'),
                    count_if(execution_status IN ('FINISHED', 'FAILED'))) AS sql_success_rate
  FROM genie_sql s LEFT JOIN workspaces w ON w.workspace_id = s.workspace_id
  WHERE {CUR.replace('event_date', 's.event_date')} GROUP BY 1),
ws_fb AS (
  SELECT a.workspace_name, try_divide(sum(is_positive), sum(is_positive) + sum(is_negative)) AS positive_feedback_rate
  FROM feedback f JOIN agent_labels a ON a.agent_id = f.agent_id
  WHERE {CUR.replace('event_date', 'f.event_date')} GROUP BY 1),
ws_list AS (SELECT DISTINCT workspace_name FROM ws_q),
total_q AS (SELECT count(*) AS n FROM questions WHERE {CUR})
SELECT
  l.workspace_name,
  coalesce(c.active_users, 0) AS active_users,
  coalesce(p.active_users_prior, 0) AS active_users_prior,
  try_divide(coalesce(c.active_users, 0) - p.active_users_prior, p.active_users_prior) AS active_users_change_pct,
  coalesce(c.questions, 0) AS questions,
  coalesce(p.questions_prior, 0) AS questions_prior,
  try_divide(coalesce(c.questions, 0) - p.questions_prior, p.questions_prior) AS questions_change_pct,
  coalesce(c.active_agents, 0) AS active_agents,
  (SELECT count(*) FROM agent_labels x WHERE x.workspace_name = l.workspace_name AND x.trashed_date IS NULL)
    AS agents_total,
  (SELECT count(*) FROM first_seen fs WHERE fs.workspace_name = l.workspace_name
     AND fs.first_date >= date_sub(current_date(), 30) AND fs.first_date < current_date()) AS new_users,
  try_divide(c.questions, c.active_users) AS questions_per_user,
  s.sql_success_rate,
  fb.positive_feedback_rate,
  round(coalesce(k.billable, 0), 2) AS billable_cost_usd,
  round(coalesce(k.free_value, 0), 2) AS free_usage_value_usd,
  round(coalesce(m.compute, 0), 2) AS sql_compute_cost_usd,
  round(coalesce(k.billable, 0) + coalesce(m.compute, 0), 2) AS total_cost_usd,
  round(try_divide(coalesce(k.billable, 0) + coalesce(m.compute, 0), c.active_users), 2) AS cost_per_active_user,
  (SELECT count(*) FROM ag WHERE ag.workspace_name = l.workspace_name
     AND ag.health_status IN ('Needs attention', 'Declining')) AS agents_needing_attention,
  try_divide(coalesce(c.questions, 0), (SELECT n FROM total_q)) AS share_of_questions_pct
FROM ws_list l
LEFT JOIN (SELECT workspace_name, count(*) AS questions, count(DISTINCT user_email) AS active_users,
                  count(DISTINCT agent_id) AS active_agents
           FROM ws_q WHERE {CUR} GROUP BY 1) c ON c.workspace_name = l.workspace_name
LEFT JOIN (SELECT workspace_name, count(*) AS questions_prior, count(DISTINCT user_email) AS active_users_prior
           FROM ws_q WHERE {PRIOR} GROUP BY 1) p ON p.workspace_name = l.workspace_name
LEFT JOIN ws_sql s ON s.workspace_name = l.workspace_name
LEFT JOIN ws_fb fb ON fb.workspace_name = l.workspace_name
LEFT JOIN ws_cost k ON k.workspace_name = l.workspace_name
LEFT JOIN ws_compute m ON m.workspace_name = l.workspace_name"""


WORKSPACE_COLUMNS = (
    "workspace_name", "active_users", "active_users_prior", "active_users_change_pct", "questions",
    "questions_prior", "questions_change_pct", "active_agents", "agents_total", "new_users",
    "questions_per_user", "sql_success_rate", "positive_feedback_rate", "billable_cost_usd",
    "free_usage_value_usd", "sql_compute_cost_usd", "total_cost_usd", "cost_per_active_user",
    "agents_needing_attention", "share_of_questions_pct",
)


def _monthly_trend(with_dim):
    return with_ctes(cte_questions(), cte_prices(), cte_genie_billing(), cte_sql_compute()) + """
, first_seen AS (SELECT user_email, min(event_date) AS first_date FROM questions GROUP BY 1),
months AS (SELECT DISTINCT trunc(event_date, 'MM') AS month FROM questions),
q AS (SELECT trunc(event_date, 'MM') AS month, count(*) AS questions, count(DISTINCT user_email) AS active_users,
             count(DISTINCT agent_id) AS active_agents FROM questions GROUP BY 1),
n AS (SELECT trunc(first_date, 'MM') AS month, count(*) AS new_users FROM first_seen GROUP BY 1),
b AS (SELECT trunc(event_date, 'MM') AS month, sum(billable_cost_usd) AS billable,
             sum(free_usage_value_usd) AS free_value FROM genie_billing GROUP BY 1),
c AS (SELECT trunc(event_date, 'MM') AS month, sum(sql_compute_cost_usd) AS compute
      FROM genie_sql_compute GROUP BY 1)
SELECT m.month,
       coalesce(q.active_users, 0) AS active_users,
       coalesce(q.questions, 0) AS questions,
       coalesce(q.active_agents, 0) AS active_agents,
       coalesce(n.new_users, 0) AS new_users,
       round(coalesce(b.billable, 0), 2) AS billable_cost_usd,
       round(coalesce(b.free_value, 0), 2) AS free_usage_value_usd,
       round(coalesce(c.compute, 0), 2) AS sql_compute_cost_usd,
       m.month = trunc(current_date(), 'MM') AS is_partial_month
FROM months m
LEFT JOIN q ON q.month = m.month LEFT JOIN n ON n.month = m.month
LEFT JOIN b ON b.month = m.month LEFT JOIN c ON c.month = m.month
ORDER BY m.month"""


def _daily_activity(with_dim):
    return with_ctes(*agent_ctes(with_dim), cte_conversations()) + """
SELECT q.event_date, a.workspace_name, a.agent_label, q.user_email, q.channel, q.mode,
       count(*) AS questions
FROM questions q JOIN agent_labels a ON a.agent_id = q.agent_id
GROUP BY ALL"""


def _quality_daily(with_dim):
    return with_ctes(*agent_ctes(with_dim), cte_feedback(), cte_comments(), cte_genie_sql()) + """
, s AS (SELECT event_date, agent_id,
               count_if(execution_status = 'FINISHED') AS sql_succeeded,
               count_if(execution_status = 'FAILED') AS sql_failed,
               count_if(execution_status = 'CANCELED') AS sql_canceled,
               count_if(execution_status = 'FINISHED' AND produced_rows = 0) AS empty_results
        FROM genie_sql GROUP BY 1, 2),
f AS (SELECT event_date, agent_id, sum(is_positive) AS thumbs_up, sum(is_negative) AS thumbs_down
      FROM feedback GROUP BY 1, 2),
c AS (SELECT event_date, agent_id, count(*) AS comments FROM comments GROUP BY 1, 2),
k AS (SELECT event_date, agent_id FROM s UNION SELECT event_date, agent_id FROM f
      UNION SELECT event_date, agent_id FROM c)
SELECT k.event_date, a.workspace_name, a.agent_label,
       coalesce(s.sql_succeeded, 0) AS sql_succeeded, coalesce(s.sql_failed, 0) AS sql_failed,
       coalesce(s.sql_canceled, 0) AS sql_canceled, coalesce(s.empty_results, 0) AS empty_results,
       coalesce(f.thumbs_up, 0) AS thumbs_up, coalesce(f.thumbs_down, 0) AS thumbs_down,
       coalesce(c.comments, 0) AS comments
FROM k JOIN agent_labels a ON a.agent_id = k.agent_id
LEFT JOIN s ON s.event_date = k.event_date AND s.agent_id = k.agent_id
LEFT JOIN f ON f.event_date = k.event_date AND f.agent_id = k.agent_id
LEFT JOIN c ON c.event_date = k.event_date AND c.agent_id = k.agent_id"""


def _cost_daily(with_dim):
    return with_ctes(*agent_ctes(with_dim), cte_prices(), cte_genie_billing(), cte_sql_compute()) + """
, x AS (
  SELECT event_date, workspace_id, agent_id, user_email, channel, dbus AS genie_dbus,
         billable_cost_usd, free_usage_value_usd, 0D AS sql_compute_cost_usd FROM genie_billing
  UNION ALL
  SELECT event_date, workspace_id, agent_id, user_email, 'SQL compute' AS channel, 0D,
         0D, 0D, sql_compute_cost_usd FROM genie_sql_compute)
SELECT x.event_date,
       coalesce(a.workspace_name, w.workspace_name, x.workspace_id) AS workspace_name,
       coalesce(a.agent_label, 'Unattributed') AS agent_label,
       coalesce(x.user_email, 'unknown') AS user_email,
       x.channel,
       round(sum(genie_dbus), 4) AS genie_dbus,
       round(sum(billable_cost_usd), 4) AS billable_cost_usd,
       round(sum(free_usage_value_usd), 4) AS free_usage_value_usd,
       round(sum(sql_compute_cost_usd), 4) AS sql_compute_cost_usd,
       round(sum(billable_cost_usd) + sum(sql_compute_cost_usd), 4) AS total_cost_usd
FROM x LEFT JOIN agent_labels a ON a.agent_id = x.agent_id
LEFT JOIN workspaces w ON w.workspace_id = x.workspace_id
GROUP BY ALL"""


def _hourly_pattern(with_dim):
    return with_ctes(*agent_ctes(with_dim)) + """
SELECT a.workspace_name, a.agent_label,
       weekday(q.event_time) + 1 AS day_of_week,
       date_format(q.event_time, 'E') AS day_name,
       hour(q.event_time) AS hour_of_day,
       count(*) AS questions
FROM questions q JOIN agent_labels a ON a.agent_id = q.agent_id
WHERE q.event_date >= date_sub(current_date(), 90)
GROUP BY ALL"""


def _user_kpis(with_dim):
    return with_ctes(*agent_ctes(with_dim), cte_prices(), cte_genie_billing()) + f"""
, uq AS (SELECT q.*, a.workspace_name FROM questions q JOIN agent_labels a ON a.agent_id = q.agent_id),
life AS (SELECT user_email, workspace_name, min(event_date) AS first_seen, max(event_date) AS last_seen
         FROM uq GROUP BY 1, 2),
cur AS (SELECT user_email, workspace_name, count(*) AS questions_30d,
               count(DISTINCT event_date) AS active_days_30d, count(DISTINCT agent_id) AS agents_used_30d,
               mode(agent_id) AS top_agent_id
        FROM uq WHERE {CUR} GROUP BY 1, 2),
cost AS (SELECT b.user_email, a.workspace_name, sum(b.billable_cost_usd) AS billable,
                sum(b.free_usage_value_usd) AS free_value
         FROM genie_billing b JOIN agent_labels a ON a.agent_id = b.agent_id
         WHERE {CUR.replace('event_date', 'b.event_date')} GROUP BY 1, 2)
SELECT l.user_email, l.workspace_name,
       coalesce(c.questions_30d, 0) AS questions_30d,
       coalesce(c.active_days_30d, 0) AS active_days_30d,
       coalesce(c.agents_used_30d, 0) AS agents_used_30d,
       ta.agent_label AS top_agent,
       l.first_seen, l.last_seen,
       CASE WHEN l.first_seen >= date_sub(current_date(), 30) THEN 'New'
            WHEN coalesce(c.questions_30d, 0) = 0 THEN 'Dormant'
            WHEN c.active_days_30d >= 8 OR c.questions_30d >= 50 THEN 'Power'
            WHEN c.active_days_30d >= 3 THEN 'Regular'
            ELSE 'Casual' END AS user_segment,
       round(coalesce(k.billable, 0), 2) AS billable_cost_usd_30d,
       round(coalesce(k.free_value, 0), 2) AS free_usage_value_usd_30d
FROM life l
LEFT JOIN cur c ON c.user_email = l.user_email AND c.workspace_name = l.workspace_name
LEFT JOIN agent_labels ta ON ta.agent_id = c.top_agent_id
LEFT JOIN cost k ON k.user_email = l.user_email AND k.workspace_name = l.workspace_name"""


def _retention_cohort(with_dim):
    return with_ctes(cte_questions()) + """
, firsts AS (SELECT user_email, trunc(min(event_date), 'MM') AS cohort_month FROM questions GROUP BY 1),
activity AS (SELECT DISTINCT user_email, trunc(event_date, 'MM') AS active_month FROM questions),
sizes AS (SELECT cohort_month, count(*) AS cohort_size FROM firsts GROUP BY 1)
SELECT f.cohort_month,
       CAST(months_between(a.active_month, f.cohort_month) AS INT) AS months_since_first,
       concat('Month ', CAST(months_between(a.active_month, f.cohort_month) AS INT)) AS month_label,
       s.cohort_size,
       count(DISTINCT a.user_email) AS retained_users,
       round(count(DISTINCT a.user_email) / s.cohort_size, 3) AS retention_rate
FROM firsts f JOIN activity a ON a.user_email = f.user_email
JOIN sizes s ON s.cohort_month = f.cohort_month
GROUP BY ALL"""


def _definitions(with_dim):
    rows = [
        ("Active users", "Distinct people who asked at least one Genie question in the last 30 complete days."),
        ("Questions", "Messages sent to a Genie agent (UI, API, MCP and agent mode), successful requests only."),
        ("Active agents", "Genie agents (formerly Genie spaces) that received at least one question in the window."),
        ("Stickiness (DAU/MAU)", "Average daily active users divided by 30-day active users. Higher means habitual use."),
        ("User retention", "Share of last period's active users who came back this period."),
        ("SQL success rate", "Genie-generated SQL statements that finished, out of finished plus failed (system.query.history)."),
        ("Empty result rate", "Finished Genie SQL statements that returned zero rows. A spike can mean the agent misunderstands filters."),
        ("Positive feedback rate", "Positive ratings out of positive plus negative ratings (latest rating per message)."),
        ("Genie billable cost", "Genie pay-as-you-go usage at list price (billing_origin_product = GENIE, excluding free usage). Corrections (RETRACTION/RESTATEMENT rows) net out automatically."),
        ("Free usage value", "Genie usage billed to the free SKU, valued at the list price of the account's billable Genie SKU. Shows what this usage would cost once free usage ends."),
        ("SQL compute cost (est.)", "Each warehouse's daily list-price cost, attributed to Genie agents by their share of that warehouse's query time."),
        ("Total cost", "Genie billable cost plus estimated SQL compute cost. List prices only: negotiated discounts are not reflected."),
        ("Projected cost after free period", "Total cost plus free usage value for the same 30 days."),
        ("Agent health", "Trashed, Unused (never asked), Stale (no questions in 30 days), Needs attention (10+ questions and SQL success under 90% or positive feedback under 60%), Declining (questions down more than half), Growing, Healthy."),
        ("User segment", "New (first question in last 30 days), Power (8+ active days or 50+ questions), Regular (3+ days), Casual, Dormant (no questions in 30 days)."),
        ("Agent names", "From the genie_agent_dim lookup table when present, otherwise the latest name seen in the audit log. Unnamed agents show the last 6 characters of their ID."),
    ]
    values = ",\n  ".join("('{}', '{}')".format(k, v.replace("'", "''")) for k, v in rows)
    return f"SELECT * FROM VALUES\n  {values}\nAS t(kpi, definition)"


_SPECS = [
    ("ds_exec_kpis", "Executive KPIs (last 30 days)", EXEC_COLUMNS, _exec_kpis_full, False),
    ("ds_monthly_trend", "Monthly trend", ("month", "active_users", "questions", "active_agents", "new_users",
     "billable_cost_usd", "free_usage_value_usd", "sql_compute_cost_usd", "is_partial_month"), _monthly_trend, False),
    ("ds_workspace_kpis", "Workspace KPIs (last 30 days)", WORKSPACE_COLUMNS, _workspace_kpis, False),
    ("ds_agent_kpis", "Agent KPIs (last 30 days)", AGENT_COLUMNS, _agent_kpis, False),
    ("ds_daily_activity", "Daily activity", ("event_date", "workspace_name", "agent_label", "user_email", "channel",
     "mode", "questions"), _daily_activity, False),
    ("ds_quality_daily", "Daily quality", ("event_date", "workspace_name", "agent_label", "sql_succeeded",
     "sql_failed", "sql_canceled", "empty_results", "thumbs_up", "thumbs_down", "comments"), _quality_daily, False),
    ("ds_cost_daily", "Daily cost", ("event_date", "workspace_name", "agent_label", "user_email", "channel",
     "genie_dbus", "billable_cost_usd", "free_usage_value_usd", "sql_compute_cost_usd", "total_cost_usd"),
     _cost_daily, False),
    ("ds_hourly_pattern", "Usage by hour (last 90 days)", ("workspace_name", "agent_label", "day_of_week",
     "day_name", "hour_of_day", "questions"), _hourly_pattern, False),
    ("ds_user_kpis", "User KPIs", ("user_email", "workspace_name", "questions_30d", "active_days_30d",
     "agents_used_30d", "top_agent", "first_seen", "last_seen", "user_segment", "billable_cost_usd_30d",
     "free_usage_value_usd_30d"), _user_kpis, False),
    ("ds_retention_cohort", "Monthly retention cohorts", ("cohort_month", "months_since_first", "month_label",
     "cohort_size", "retained_users", "retention_rate"), _retention_cohort, False),
    ("ds_definitions", "KPI definitions", ("kpi", "definition"), _definitions, False),
]


def build_datasets(with_dim=True):
    return [Dataset(n, d, c, b, e, with_dim) for n, d, c, b, e in _SPECS]


DATASETS = build_datasets(with_dim=True)


def dataset(name, with_dim=True):
    return next(d for d in build_datasets(with_dim) if d.name == name)
