WITH workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
),
questions AS (SELECT * FROM __TARGET__.wt_base_questions),
agent_labels AS (SELECT * FROM __TARGET__.wt_base_agents),
conversations AS (SELECT * FROM __TARGET__.wt_base_conversations)
SELECT q.event_date, a.workspace_name, a.agent_label, q.user_email, q.channel, q.mode,
       count(*) AS questions
FROM questions q JOIN agent_labels a ON a.agent_id = q.agent_id
GROUP BY ALL
