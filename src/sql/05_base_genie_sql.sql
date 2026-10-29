WITH genie_sql AS (
  SELECT DATE(start_time) AS event_date,
         CAST(workspace_id AS STRING) AS workspace_id,
         query_source.genie_space_id AS agent_id,
         lower(executed_by) AS user_email,
         execution_status,
         total_duration_ms,
         produced_rows
  FROM system.query.history
  WHERE query_source.genie_space_id IS NOT NULL
    AND start_time >= add_months(trunc(current_date(), 'MM'), -12)
)
SELECT * FROM genie_sql
