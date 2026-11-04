-- Monthly totals per region. `*_days` = how many days of the month the source covers: partial months
-- (the first and last month of each source) must not be compared with full ones.
select
    iso_code,
    region,
    name_uk,
    date_trunc('month', day)::date as month,
    round(sum(any_alert_hours), 2) as any_alert_hours,
    round(sum(oblastwide_alert_hours), 2) as oblastwide_alert_hours,
    round(sum(volunteer_alert_hours), 2) as volunteer_alert_hours,
    sum(any_episodes) as any_episodes,
    sum(volunteer_episodes) as volunteer_episodes,
    count(any_alert_hours) as official_days,
    count(volunteer_alert_hours) as volunteer_days,
    count(*) as days_in_month
from {{ ref('agg_region_daily') }}
group by all
