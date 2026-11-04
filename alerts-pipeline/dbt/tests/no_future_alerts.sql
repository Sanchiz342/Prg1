-- Alerts cannot start in the future (would indicate a time-zone or parsing error).
select alert_id, started_at
from {{ ref('fct_alerts') }}
where started_at > now() + interval 1 hour
