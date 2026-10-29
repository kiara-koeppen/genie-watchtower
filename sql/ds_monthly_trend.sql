WITH questions AS (SELECT * FROM __TARGET__.wt_base_questions),
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
ORDER BY m.month
