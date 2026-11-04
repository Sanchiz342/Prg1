-- Merged episodes of the same metric and oblast must not overlap or touch (they would have been merged).
select a.metric, a.oblast, a.episode_no, b.episode_no as other
from {{ ref('int_alert_episodes') }} as a
join {{ ref('int_alert_episodes') }} as b
  on a.metric = b.metric and a.oblast = b.oblast and a.episode_no < b.episode_no
 and a.finished_at >= b.started_at and b.finished_at >= a.started_at
