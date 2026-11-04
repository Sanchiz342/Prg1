-- Every alert record from both sources, with region attributes. Incremental: each run rebuilds the last
-- `incremental_lookback_days` days of alerts (by start day), so upstream corrections inside that window are
-- absorbed; `--full-refresh` rebuilds everything.
{{
    config(
        materialized='incremental',
        unique_key='started_day',
        incremental_strategy='delete+insert',
        on_schema_change='fail'
    )
}}

with alerts as (
    select * from {{ ref('stg_official_alerts') }}
    union all
    select * from {{ ref('stg_volunteer_alerts') }}
)

select
    a.alert_id,
    a.source,
    a.oblast,
    r.iso_code,
    r.name_uk,
    a.raion,
    a.hromada,
    a.level,
    a.started_at,
    a.finished_at,
    timezone('{{ var("timezone") }}', a.started_at)::date as started_day,
    a.duration_minutes,
    a.is_naive,
    a.duration_minutes > {{ var('long_alert_hours') }} * 60 as is_long
from alerts as a
left join {{ ref('regions') }} as r on r.region = a.oblast
{% if is_incremental() %}
where timezone('{{ var("timezone") }}', a.started_at)::date
    >= (select max(started_day) from {{ this }}) - {{ var('incremental_lookback_days') }}
{% endif %}
