WITH questions AS (
  SELECT
    a.event_date,
    a.event_time,
    CAST(a.workspace_id AS STRING) AS workspace_id,
    a.request_params['space_id'] AS agent_id,
    lower(a.user_identity.email) AS user_email,
    CASE WHEN a.action_name LIKE 'genie%' THEN 'API' ELSE 'UI' END AS channel,
    CASE WHEN a.action_name ILIKE '%agentic%' THEN 'Agent mode' ELSE 'Standard' END AS mode
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie'
    AND a.action_name IN ('createConversationMessage', 'createAgenticResponse', 'genieStartConversationMessage', 'genieCreateConversationMessage', 'genieCreateAgenticResponse')
    AND a.response.status_code = 200
    AND a.request_params['space_id'] IS NOT NULL
    AND a.event_date >= add_months(trunc(current_date(), 'MM'), -12)
)
SELECT * FROM questions
