-- Splits every episode across the Kyiv-local calendar days it touches. Day boundaries are local midnights
-- converted to absolute time, so days with a daylight-saving switch correctly have 23 or 25 hours.
{% set tz = var('timezone') %}
with episodes as (
    select
        *,
        timezone('{{ tz }}', started_at)::date as first_day,
        timezone('{{ tz }}', finished_at)::date as last_day
    from {{ ref('int_alert_episodes') }}
    where finished_at > started_at
),

days as (
    select
        metric,
        oblast,
        started_at,
        finished_at,
        unnest(generate_series(first_day, last_day, interval 1 day))::date as day
    from episodes
),

bounded as (
    select
        *,
        timezone('{{ tz }}', day::timestamp) as day_start,
        timezone('{{ tz }}', (day + 1)::timestamp) as day_end
    from days
)

select
    metric,
    oblast,
    day,
    sum(epoch(least(finished_at, day_end) - greatest(started_at, day_start))) / 60.0 as minutes
from bounded
where least(finished_at, day_end) > greatest(started_at, day_start)
group by metric, oblast, day
