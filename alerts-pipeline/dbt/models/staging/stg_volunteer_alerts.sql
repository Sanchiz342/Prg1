-- Typed volunteer alerts (oblast level only). `is_naive`: the end time was not reported and is start + 30 min.
with typed as (
    select
        trim(region) as oblast,
        try_cast(started_at as timestamptz) as started_at,
        try_cast(finished_at as timestamptz) as finished_at,
        lower(trim(naive)) = 'true' as is_naive
    from {{ source('raw', 'volunteer_alerts') }}
),

valid as (
    select *
    from typed
    where started_at is not null and finished_at is not null and finished_at >= started_at
),

numbered as (
    select *, row_number() over (partition by oblast, started_at, finished_at, is_naive) as copy_no
    from valid
)

select
    md5(concat_ws('|', 'volunteer', oblast, started_at::varchar, finished_at::varchar, is_naive::varchar)) as alert_id,
    'volunteer' as source,
    oblast,
    null::varchar as raion,
    null::varchar as hromada,
    'oblast' as level,
    started_at,
    finished_at,
    epoch(finished_at - started_at) / 60.0 as duration_minutes,
    is_naive
from numbered
where copy_no = 1
