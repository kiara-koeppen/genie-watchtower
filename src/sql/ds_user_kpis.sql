WITH workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
),
questions AS (SELECT * FROM __TARGET__.wt_base_questions),
agent_labels AS (SELECT * FROM __TARGET__.wt_base_agents),
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
genie_billing AS (SELECT * FROM __TARGET__.wt_base_genie_billing)
, uq AS (SELECT q.*, a.workspace_name FROM questions q JOIN agent_labels a ON a.agent_id = q.agent_id),
life AS (SELECT user_email, workspace_name, min(event_date) AS first_seen, max(event_date) AS last_seen
         FROM uq GROUP BY 1, 2),
cur AS (SELECT user_email, workspace_name, count(*) AS questions_30d,
               count(DISTINCT event_date) AS active_days_30d, count(DISTINCT agent_id) AS agents_used_30d,
               mode(agent_id) AS top_agent_id
        FROM uq WHERE event_date >= date_sub(current_date(), 30) AND event_date < current_date() GROUP BY 1, 2),
cost AS (SELECT b.user_email, a.workspace_name, sum(b.billable_cost_usd) AS billable,
                sum(b.free_usage_value_usd) AS free_value
         FROM genie_billing b JOIN agent_labels a ON a.agent_id = b.agent_id
         WHERE b.event_date >= date_sub(current_date(), 30) AND b.event_date < current_date() GROUP BY 1, 2)
SELECT l.user_email, l.workspace_name,
       coalesce(c.questions_30d, 0) AS questions_30d,
       coalesce(c.active_days_30d, 0) AS active_days_30d,
       coalesce(c.agents_used_30d, 0) AS agents_used_30d,
       coalesce(ta.agent_label, 'None in 30 days') AS top_agent,
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
LEFT JOIN cost k ON k.user_email = l.user_email AND k.workspace_name = l.workspace_name
ORDER BY questions_30d DESC
