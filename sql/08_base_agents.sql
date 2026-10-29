WITH workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
),
questions AS (SELECT * FROM __TARGET__.wt_base_questions),
dim_names AS (
  SELECT agent_id, title AS dim_title, CAST(workspace_id AS STRING) AS dim_workspace_id
  FROM __TARGET__.genie_agent_dim
),
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
  WHERE a.service_name = 'aibiGenie' AND a.action_name IN ('createSpace', 'genieCreateSpace', 'cloneSpace')
    AND a.response.status_code = 200
  GROUP BY 1
),
trashed AS (
  SELECT a.request_params['space_id'] AS agent_id, max(a.event_date) AS trashed_date
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie' AND a.action_name IN ('trashSpace', 'genieTrashSpace')
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
  UNION SELECT agent_id FROM created WHERE created_date >= add_months(trunc(current_date(), 'MM'), -12)
  UNION SELECT agent_id FROM dim_names
),
agents AS (
  SELECT u.agent_id,
         coalesce(d.dim_title, n.audit_name) AS agent_name,
         coalesce(c.created_workspace_id, d.dim_workspace_id, q.last_workspace_id) AS workspace_id,
         c.creator, c.created_date, t.trashed_date, q.last_used_date
  FROM agent_universe u
  LEFT JOIN audit_names n ON n.agent_id = u.agent_id
  LEFT JOIN dim_names d ON d.agent_id = u.agent_id
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
)
SELECT * FROM agent_labels
