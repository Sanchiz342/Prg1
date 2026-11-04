-- No region can be under alert for more than the length of a day (25 h on the autumn DST day), and never < 0.
select *
from {{ ref('int_episode_days') }}
where minutes < 0 or minutes > 25 * 60 + 0.01
