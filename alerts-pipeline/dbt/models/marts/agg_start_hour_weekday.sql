-- When do alerts start? Episode starts by Kyiv-local hour and ISO weekday (1 = Monday), per region and
-- metric. Episodes (not raw records) are counted, so simultaneous raion alerts count once.
select
    e.metric,
    r.iso_code,
    extract(isodow from timezone('{{ var("timezone") }}', e.started_at)) as weekday,
    extract(hour from timezone('{{ var("timezone") }}', e.started_at)) as hour,
    count(*) as episodes
from {{ ref('int_alert_episodes') }} as e
join {{ ref('regions') }} as r on r.region = e.oblast
where e.metric in ('official_any', 'volunteer')
group by all
