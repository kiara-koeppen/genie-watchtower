WITH feedback AS (
  SELECT event_date, agent_id, user_email,
         CASE WHEN rating IN ('THUMBS_UP', 'POSITIVE') THEN 1 ELSE 0 END AS is_positive,
         CASE WHEN rating IN ('THUMBS_DOWN', 'NEGATIVE') THEN 1 ELSE 0 END AS is_negative
  FROM (
    SELECT a.event_date, a.request_params['space_id'] AS agent_id,
           lower(a.user_identity.email) AS user_email,
           coalesce(a.request_params['feedback_rating'], a.request_params['rating']) AS rating,
           row_number() OVER (PARTITION BY a.request_params['message_id'] ORDER BY a.event_time DESC) AS rn
    FROM system.access.audit a
    WHERE a.service_name = 'aibiGenie'
      AND a.action_name IN ('updateConversationMessageFeedback', 'genieSendMessageFeedback')
      AND a.request_params['space_id'] IS NOT NULL
      AND a.event_date >= add_months(trunc(current_date(), 'MM'), -12)
  )
  WHERE rn = 1 AND rating IN ('THUMBS_UP', 'POSITIVE', 'THUMBS_DOWN', 'NEGATIVE')
)
SELECT * FROM feedback
