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
genie_billing AS (
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
    AND u.usage_date >= add_months(trunc(current_date(), 'MM'), -12)
)
SELECT * FROM genie_billing
