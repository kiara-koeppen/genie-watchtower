WITH prices AS (
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
wh_cost AS (
  SELECT u.usage_date AS event_date, u.usage_metadata.warehouse_id AS warehouse_id,
         SUM(u.usage_quantity * coalesce(p.unit_price, 0)) AS cost
  FROM system.billing.usage u
  LEFT JOIN prices p
    ON u.sku_name = p.sku_name AND u.cloud = p.cloud
   AND u.usage_start_time >= p.price_start_time
   AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_metadata.warehouse_id IS NOT NULL AND u.usage_date >= add_months(trunc(current_date(), 'MM'), -12)
  GROUP BY ALL
),
wh_time AS (
  SELECT DATE(start_time) AS event_date, compute.warehouse_id AS warehouse_id,
         CAST(workspace_id AS STRING) AS workspace_id,
         query_source.genie_space_id AS agent_id, lower(executed_by) AS user_email,
         SUM(total_duration_ms) AS ms
  FROM system.query.history
  WHERE compute.warehouse_id IS NOT NULL AND start_time >= add_months(trunc(current_date(), 'MM'), -12)
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
)
SELECT * FROM genie_sql_compute
