-- Typed, de-duplicated official alerts. Rows that cannot be parsed or end before they start are dropped here
-- and counted in rpt_data_quality; exact duplicate rows (the published file contains many) are kept once.
with typed as (
    select
        trim(oblast) as oblast,
        nullif(trim(raion), '') as raion,
        nullif(trim(hromada), '') as hromada,
        lower(trim(level)) as level,
        try_cast(started_at as timestamptz) as started_at,
        try_cast(finished_at as timestamptz) as finished_at
    from {{ source('raw', 'official_alerts') }}
),

valid as (
    select *
    from typed
    where started_at is not null and finished_at is not null and finished_at >= started_at
),

numbered as (
    select
        *,
        row_number() over (partition by oblast, raion, hromada, level, started_at, finished_at) as copy_no
    from valid
)

select
    md5(concat_ws('|', 'official', oblast, coalesce(raion, ''), coalesce(hromada, ''), level,
                  started_at::varchar, finished_at::varchar)) as alert_id,
    'official' as source,
    oblast,
    raion,
    hromada,
    level,
    started_at,
    finished_at,
    epoch(finished_at - started_at) / 60.0 as duration_minutes,
    false as is_naive
from numbered
where copy_no = 1
