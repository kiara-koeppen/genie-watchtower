WITH comments AS (
  SELECT a.event_date, a.request_params['space_id'] AS agent_id
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie' AND a.action_name = 'createConversationMessageComment'
    AND a.request_params['space_id'] IS NOT NULL AND a.event_date >= add_months(trunc(current_date(), 'MM'), -12)
)
SELECT * FROM comments
