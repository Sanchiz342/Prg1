-- Splitting episodes into days must neither lose nor invent time: per metric, the minutes over all days equal
-- the total episode duration (within a rounding tolerance of 0.1 minute).
with episodes as (
    select metric, sum(duration_minutes) as minutes from {{ ref('int_alert_episodes') }} group by metric
),
days as (
    select metric, sum(minutes) as minutes from {{ ref('int_episode_days') }} group by metric
)
select e.metric, e.minutes as episode_minutes, d.minutes as day_minutes
from episodes as e
full join days as d using (metric)
where abs(coalesce(e.minutes, 0) - coalesce(d.minutes, 0)) > 0.1
