WITH workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
),
questions AS (SELECT * FROM __TARGET__.wt_base_questions),
agent_labels AS (SELECT * FROM __TARGET__.wt_base_agents),
conversations AS (SELECT * FROM __TARGET__.wt_base_conversations),
feedback AS (SELECT * FROM __TARGET__.wt_base_feedback),
comments AS (SELECT * FROM __TARGET__.wt_base_comments),
curation AS (SELECT * FROM __TARGET__.wt_base_curation),
genie_sql AS (SELECT * FROM __TARGET__.wt_base_genie_sql),
prices AS (
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
),
genie_billing AS (SELECT * FROM __TARGET__.wt_base_genie_billing),
genie_sql_compute AS (SELECT * FROM __TARGET__.wt_base_sql_compute)
SELECT
  a.agent_id, a.agent_label, a.agent_name, a.workspace_name, coalesce(a.creator, 'Unknown') AS creator,
  a.created_date,
  a.last_used_date,
  datediff(current_date(), a.last_used_date) AS days_since_last_use,
  ed.last_edited_date,
  coalesce(q.active_users, 0) AS active_users,
  coalesce(qp.active_users_prior, 0) AS active_users_prior,
  coalesce(q.questions, 0) AS questions,
  coalesce(qp.questions_prior, 0) AS questions_prior,
  try_divide(coalesce(q.questions, 0) - qp.questions_prior, qp.questions_prior) AS questions_change_pct,
  CASE WHEN coalesce(qp.questions_prior, 0) = 0 THEN 'New'
       ELSE concat(CASE WHEN q.questions >= qp.questions_prior THEN '+' ELSE '' END,
                   CAST(round(try_divide(coalesce(q.questions, 0) - qp.questions_prior, qp.questions_prior) * 100) AS INT), '%')
  END AS questions_change_label,
  coalesce(cast(round(try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) * 100) AS INT) || '%', 'No ratings')
    AS positive_feedback_label,
  coalesce(cast(round(s.sql_success_rate * 100, 1) AS STRING) || '%', 'No SQL') AS sql_success_label,
  coalesce(cv.conversations, 0) AS conversations,
  try_divide(q.questions, cv.conversations) AS questions_per_conversation,
  try_divide(q.api_questions, q.questions) AS api_share_pct,
  coalesce(s.sql_queries, 0) AS sql_queries,
  s.sql_success_rate, s.empty_result_rate, s.p50_response_s, s.p95_response_s,
  round(s.sql_success_rate * 100, 1) AS sql_success_pct,
  round(s.empty_result_rate * 100, 1) AS empty_result_pct,
  coalesce(f.thumbs_up, 0) AS thumbs_up,
  coalesce(f.thumbs_down, 0) AS thumbs_down,
  try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) AS positive_feedback_rate,
  round(try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) * 100, 1) AS positive_feedback_pct,
  coalesce(cm.comments, 0) AS comments,
  coalesce(cu.curation_events, 0) AS curation_events_30d,
  coalesce(cu.benchmark_runs, 0) AS benchmark_runs_30d,
  round(coalesce(b.billable_cost_usd, 0), 2) AS billable_cost_usd,
  round(coalesce(b.free_usage_value_usd, 0), 2) AS free_usage_value_usd,
  round(coalesce(sc.sql_compute_cost_usd, 0), 2) AS sql_compute_cost_usd,
  round(coalesce(b.billable_cost_usd, 0) + coalesce(sc.sql_compute_cost_usd, 0), 2) AS total_cost_usd,
  round(try_divide(coalesce(b.billable_cost_usd, 0) + coalesce(sc.sql_compute_cost_usd, 0), q.questions), 3)
    AS cost_per_question,
  coalesce(q.top_user, 'None in 30 days') AS top_user,
  CASE
    WHEN a.trashed_date IS NOT NULL THEN 'Trashed'
    WHEN a.last_used_date IS NULL THEN 'Unused'
    WHEN coalesce(q.questions, 0) = 0 THEN 'Stale'
    WHEN q.questions >= 10 AND (s.sql_success_rate < 0.9
         OR (f.thumbs_up + f.thumbs_down >= 5 AND try_divide(f.thumbs_up, f.thumbs_up + f.thumbs_down) < 0.6))
      THEN 'Needs attention'
    WHEN qp.questions_prior >= 10 AND q.questions < 0.5 * qp.questions_prior THEN 'Declining'
    WHEN coalesce(qp.questions_prior, 0) = 0 THEN 'New'
    WHEN q.questions >= 1.5 * qp.questions_prior THEN 'Growing'
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
  FROM questions WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) q ON q.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, count(*) AS questions_prior, count(DISTINCT user_email) AS active_users_prior
  FROM questions WHERE event_date >= date_sub(current_date(), 60) AND event_date < date_sub(current_date(), 30) GROUP BY 1) qp ON qp.agent_id = a.agent_id
LEFT JOIN (SELECT agent_id, count(*) AS conversations FROM conversations WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) cv
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
  FROM genie_sql WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) s ON s.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, sum(is_positive) AS thumbs_up, sum(is_negative) AS thumbs_down
  FROM feedback WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) f ON f.agent_id = a.agent_id
LEFT JOIN (SELECT agent_id, count(*) AS comments FROM comments WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) cm
  ON cm.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, count_if(is_benchmark = 0) AS curation_events, sum(is_benchmark) AS benchmark_runs
  FROM curation WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) cu ON cu.agent_id = a.agent_id
LEFT JOIN (SELECT agent_id, max(event_date) AS last_edited_date FROM curation WHERE is_benchmark = 0 GROUP BY 1) ed
  ON ed.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, sum(billable_cost_usd) AS billable_cost_usd, sum(free_usage_value_usd) AS free_usage_value_usd
  FROM genie_billing WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) b ON b.agent_id = a.agent_id
LEFT JOIN (
  SELECT agent_id, sum(sql_compute_cost_usd) AS sql_compute_cost_usd
  FROM genie_sql_compute WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1) sc ON sc.agent_id = a.agent_id
-- Trashed agents only matter while they still get questions in the window.
WHERE a.trashed_date IS NULL OR q.questions > 0
ORDER BY questions DESC, agent_label
