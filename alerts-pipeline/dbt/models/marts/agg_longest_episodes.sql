-- The longest continuous alert episodes per metric (top 25 each).
with ranked as (
    select
        e.*,
        r.iso_code,
        r.name_uk,
        row_number() over (partition by e.metric order by e.duration_minutes desc, e.started_at) as rank
    from {{ ref('int_alert_episodes') }} as e
    join {{ ref('regions') }} as r on r.region = e.oblast
)

select
    metric,
    rank,
    iso_code,
    name_uk,
    started_at,
    finished_at,
    round(duration_minutes / 60.0, 1) as hours,
    alerts_merged
from ranked
where rank <= 25
