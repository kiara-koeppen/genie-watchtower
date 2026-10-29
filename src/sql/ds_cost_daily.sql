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
genie_billing AS (SELECT * FROM __TARGET__.wt_base_genie_billing),
genie_sql_compute AS (SELECT * FROM __TARGET__.wt_base_sql_compute)
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
GROUP BY ALL
