-- One row per region per Kyiv-local day (zero-filled), hours under alert by metric and episodes started.
-- Days outside a source's coverage are NULL for that source, not 0: "no data" is not "no alerts".
with coverage as (
    select
        'official' as source,
        min(timezone('{{ var("timezone") }}', started_at)::date) as first_day,
        max(timezone('{{ var("timezone") }}', started_at)::date) as last_day
    from {{ ref('stg_official_alerts') }}
    union all
    select
        'volunteer',
        min(timezone('{{ var("timezone") }}', started_at)::date),
        max(timezone('{{ var("timezone") }}', started_at)::date)
    from {{ ref('stg_volunteer_alerts') }}
),

calendar as (
    select unnest(generate_series(
        date '{{ var("calendar_start") }}', (select max(last_day) from coverage), interval 1 day
    ))::date as day
),

spine as (
    select r.region, r.iso_code, r.name_uk, c.day
    from {{ ref('regions') }} as r
    cross join calendar as c
    where r.in_dataset
),

minutes as (
    select
        oblast,
        day,
        sum(minutes) filter (where metric = 'official_any') as any_min,
        sum(minutes) filter (where metric = 'official_oblastwide') as oblastwide_min,
        sum(minutes) filter (where metric = 'volunteer') as volunteer_min
    from {{ ref('int_episode_days') }}
    group by oblast, day
),

starts as (
    select
        oblast,
        timezone('{{ var("timezone") }}', started_at)::date as day,
        count(*) filter (where metric = 'official_any') as any_episodes,
        count(*) filter (where metric = 'volunteer') as volunteer_episodes
    from {{ ref('int_alert_episodes') }}
    group by all
),

official_cov as (select * from coverage where source = 'official'),
volunteer_cov as (select * from coverage where source = 'volunteer')

select
    s.iso_code,
    s.region,
    s.name_uk,
    s.day,
    case when s.day between oc.first_day and oc.last_day
        then round(coalesce(m.any_min, 0) / 60.0, 3) end as any_alert_hours,
    case when s.day between oc.first_day and oc.last_day
        then round(coalesce(m.oblastwide_min, 0) / 60.0, 3) end as oblastwide_alert_hours,
    case when s.day between vc.first_day and vc.last_day
        then round(coalesce(m.volunteer_min, 0) / 60.0, 3) end as volunteer_alert_hours,
    case when s.day between oc.first_day and oc.last_day then coalesce(st.any_episodes, 0) end as any_episodes,
    case when s.day between vc.first_day and vc.last_day then coalesce(st.volunteer_episodes, 0) end as volunteer_episodes
from spine as s
cross join official_cov as oc
cross join volunteer_cov as vc
left join minutes as m on m.oblast = s.region and m.day = s.day
left join starts as st on st.oblast = s.region and st.day = s.day
