WITH conversations AS (
  SELECT a.event_date, a.request_params['space_id'] AS agent_id, lower(a.user_identity.email) AS user_email
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie'
    AND a.action_name IN ('createConversation', 'genieStartConversationMessage')
    AND a.response.status_code = 200
    AND a.request_params['space_id'] IS NOT NULL
    AND a.event_date >= add_months(trunc(current_date(), 'MM'), -12)
)
SELECT * FROM conversations
