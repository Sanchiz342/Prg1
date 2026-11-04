-- WARN (not error): alerts longer than long_alert_hours exist in the data. They are kept and flagged (the data
-- does not say why they are so long); this test makes them visible in every build log.
{{ config(severity='warn') }}
select alert_id, oblast, hromada, started_at, finished_at, round(duration_minutes / 60, 1) as hours
from {{ ref('fct_alerts') }}
where is_long
