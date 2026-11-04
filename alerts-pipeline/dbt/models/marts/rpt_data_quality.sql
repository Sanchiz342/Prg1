-- Data quality report: one row per check. Feeds the dashboard's "about the data" panel. Nothing here fails the
-- build (dbt tests do that); this documents what was found and how it was handled.
{% set tz = var('timezone') %}
with official_raw as (
    select
        count(*) as rows,
        count(*) filter (where try_cast(started_at as timestamptz) is null
                         or try_cast(finished_at as timestamptz) is null) as unparseable,
        count(*) filter (where try_cast(finished_at as timestamptz) < try_cast(started_at as timestamptz)) as negative
    from {{ source('raw', 'official_alerts') }}
),

volunteer_raw as (
    select
        count(*) as rows,
        count(*) filter (where try_cast(started_at as timestamptz) is null
                         or try_cast(finished_at as timestamptz) is null) as unparseable,
        count(*) filter (where try_cast(finished_at as timestamptz) < try_cast(started_at as timestamptz)) as negative
    from {{ source('raw', 'volunteer_alerts') }}
),

-- Counted from staging (always exactly the current files), not from the incremental fact table.
clean as (
    select source, count(*) as rows,
           count(*) filter (where duration_minutes > {{ var('long_alert_hours') }} * 60) as long_alerts,
           count(*) filter (where is_naive) as naive, max(started_at) as newest, min(started_at) as oldest
    from (
        select source, duration_minutes, is_naive, started_at from {{ ref('stg_official_alerts') }}
        union all
        select source, duration_minutes, is_naive, started_at from {{ ref('stg_volunteer_alerts') }}
    )
    group by source
),

checks as (
    select 'official' as source, 'raw_rows' as check_name, rows::double as value, 'info' as severity,
           'Rows in the published file' as detail
    from official_raw
    union all
    select 'official', 'exact_duplicates_removed', (o.rows - o.unparseable - o.negative - c.rows)::double,
           case when o.rows - o.unparseable - o.negative - c.rows > 0 then 'warn' else 'info' end,
           'Rows identical in every field to another row; kept once'
    from official_raw o, clean c where c.source = 'official'
    union all
    select 'official', 'unparseable_timestamps', unparseable::double,
           case when unparseable > 0 then 'warn' else 'info' end, 'Dropped: start or end is not a timestamp'
    from official_raw
    union all
    select 'official', 'ends_before_start', negative::double,
           case when negative > 0 then 'warn' else 'info' end, 'Dropped: end earlier than start'
    from official_raw
    union all
    select 'volunteer', 'raw_rows', rows::double, 'info', 'Rows in the published file' from volunteer_raw
    union all
    select 'volunteer', 'exact_duplicates_removed', (v.rows - v.unparseable - v.negative - c.rows)::double,
           case when v.rows - v.unparseable - v.negative - c.rows > 0 then 'warn' else 'info' end,
           'Rows identical in every field to another row; kept once'
    from volunteer_raw v, clean c where c.source = 'volunteer'
    union all
    select 'volunteer', 'unparseable_timestamps', unparseable::double,
           case when unparseable > 0 then 'warn' else 'info' end, 'Dropped: start or end is not a timestamp'
    from volunteer_raw
    union all
    select 'volunteer', 'estimated_end_share', round(100.0 * naive / rows, 2), 'info',
           'Percent of alerts whose end was not reported and is estimated as start + 30 min'
    from clean where source = 'volunteer'
    union all
    select source, 'long_alerts', long_alerts::double, 'info',
           'Alerts longer than {{ var("long_alert_hours") }} h: kept and flagged; the data does not record why they are so long'
    from clean
    union all
    select source, 'days_since_newest_record',
           (current_date - timezone('{{ tz }}', newest)::date)::double,
           case when current_date - timezone('{{ tz }}', newest)::date > 14 then 'warn' else 'info' end,
           'Newest alert starts on ' || strftime(timezone('{{ tz }}', newest), '%Y-%m-%d') || ' (Kyiv time)'
    from clean
    union all
    select source, 'coverage_start_day', null, 'info',
           'Oldest alert starts on ' || strftime(timezone('{{ tz }}', oldest), '%Y-%m-%d') || ' (Kyiv time)'
    from clean
    union all
    select 'both', 'unmapped_regions', count(*)::double, case when count(*) > 0 then 'error' else 'info' end,
           'Alert regions missing from the regions seed'
    from {{ ref('fct_alerts') }} where iso_code is null
)

select * from checks
