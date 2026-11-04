-- Alert EPISODES: overlapping or touching alerts in the same oblast merged into one continuous period
-- (gaps-and-islands). Without this, three raions under alert at the same time would count as three hours of
-- alert per hour. Three metrics, because what "the oblast is under alert" means changed over time:
--
--   official_any         official alerts of any level: at least part of the oblast was under alert
--   official_oblastwide  official oblast-level alerts only: the whole oblast was under alert. Comparable over
--                        time until December 2025, when alerts moved mostly to raion level.
--   volunteer            volunteer (eTryvoga) records, oblast level only: one consistent definition across the
--                        whole period, but ends of some alerts are estimated (is_naive)
--
-- Both window functions below MUST see the same total order. Many alerts share (started_at, finished_at) (e.g.
-- several hromadas alerted at the same second); with ties, the two windows may order those rows differently
-- and episode flags land on the wrong rows, non-deterministically. alert_id (unique per metric and oblast)
-- makes the order total.
with alerts as (
    select 'official_any' as metric, oblast, started_at, finished_at, alert_id from {{ ref('stg_official_alerts') }}
    union all
    select 'official_oblastwide', oblast, started_at, finished_at, alert_id from {{ ref('stg_official_alerts') }}
    where level = 'oblast'
    union all
    select 'volunteer', oblast, started_at, finished_at, alert_id from {{ ref('stg_volunteer_alerts') }}
),

ordered as (
    select
        *,
        max(finished_at) over (
            partition by metric, oblast order by started_at, finished_at, alert_id
            rows between unbounded preceding and 1 preceding
        ) as previous_end
    from alerts
),

flagged as (
    select *, case when previous_end is null or started_at > previous_end then 1 else 0 end as starts_episode
    from ordered
),

numbered as (
    select
        *,
        sum(starts_episode) over (
            partition by metric, oblast order by started_at, finished_at, alert_id
            rows between unbounded preceding and current row
        ) as episode_no
    from flagged
)

select
    metric,
    oblast,
    episode_no,
    min(started_at) as started_at,
    max(finished_at) as finished_at,
    count(*) as alerts_merged,
    epoch(max(finished_at) - min(started_at)) / 60.0 as duration_minutes
from numbered
group by metric, oblast, episode_no
