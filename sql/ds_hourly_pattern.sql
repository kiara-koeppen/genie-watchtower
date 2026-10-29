WITH workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
),
questions AS (SELECT * FROM __TARGET__.wt_base_questions),
agent_labels AS (SELECT * FROM __TARGET__.wt_base_agents)
SELECT a.workspace_name, a.agent_label,
       weekday(q.event_time) + 1 AS day_of_week,
       date_format(q.event_time, 'E') AS day_name,
       hour(q.event_time) AS hour_of_day,
       count(*) AS questions
FROM questions q JOIN agent_labels a ON a.agent_id = q.agent_id
WHERE q.event_date >= date_sub(current_date(), 90)
GROUP BY ALL
