"""dbt models on a tiny hand-made dataset whose correct answers are known exactly.

Fixture (all times UTC; Kyiv is UTC+2 in winter, UTC+3 from 2024-03-31 01:00 UTC):
  Kyiv City   one oblast alert 2024-03-30 22:30 -> 03-31 02:30, i.e. 00:30 -> 05:30 Kyiv time on 31 March, the
              night clocks jump forward; 4 h of absolute time. The row appears twice (exact duplicate).
  Kharkiv     four overlapping raion/hromada alerts 10:00-12:00 on 10 Jan (two share the same start and end:
              the tie that once broke episode numbering) -> one 2 h episode of "any" alert, none oblast-wide;
              an oblast alert 21:00 -> 01:00 = 23:00 10 Jan -> 03:00 11 Jan Kyiv: 1 h on the 10th, 3 h on the 11th;
              one unparseable row, one row ending before it starts (both dropped and counted).
  Lviv        an oblast alert of 8 days 1 hour (flagged as long, kept).
  Volunteer   Kyiv: 10:00-10:30 (estimated end) and 10:20-11:00 -> one 1 h episode; Kharkiv: 45 min on the 11th.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from pipeline.load import load_all
from pipeline.transform import run_dbt

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def wh(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("warehouse")
    for name in ("official", "volunteer"):
        (d / "raw" / name).mkdir(parents=True)
        shutil.copy(FIXTURES / f"{name}.csv", d / "raw" / name / "latest.csv")
    warehouse = d / "w.duckdb"
    load_all(warehouse=warehouse, raw_dir=d / "raw")
    outcome = run_dbt(["build"], warehouse=warehouse, work_dir=d / "dbt")
    failed = [r for r in outcome.results if r["status"] in ("fail", "error")]
    assert outcome.success, failed
    return warehouse


def q(wh: Path, sql: str):
    with duckdb.connect(str(wh), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_staging_dedupes_and_drops_invalid_rows(wh):
    assert q(wh, "select count(*) from stg_official_alerts") == [(7,)]  # 10 rows - 1 dup - 2 invalid
    quality = dict(((s, c), v) for s, c, v in q(wh, "select source, check_name, value from rpt_data_quality"))
    assert quality[("official", "raw_rows")] == 10
    assert quality[("official", "exact_duplicates_removed")] == 1
    assert quality[("official", "unparseable_timestamps")] == 1
    assert quality[("official", "ends_before_start")] == 1
    assert quality[("official", "long_alerts")] == 1
    assert quality[("volunteer", "estimated_end_share")] == pytest.approx(33.33, abs=0.01)
    assert quality[("both", "unmapped_regions")] == 0


def test_overlapping_and_tied_alerts_merge_into_one_episode(wh):
    rows = q(wh, """select metric, strftime(started_at, '%H:%M'), strftime(finished_at, '%H:%M'), alerts_merged
                    from int_alert_episodes where oblast = 'Kharkivska oblast' and started_at < '2024-01-10 20:00+00'
                    order by metric""")
    assert rows == [("official_any", "10:00", "12:00", 4)]


def test_days_are_split_at_kyiv_midnight_including_dst_night(wh):
    daily = {(r, str(d)): (a, o, v) for r, d, a, o, v in q(wh, """
        select region, day, any_alert_hours, oblastwide_alert_hours, volunteer_alert_hours from agg_region_daily
        where day in ('2024-01-10', '2024-01-11', '2024-03-30', '2024-03-31')""")}
    assert daily[("Kharkivska oblast", "2024-01-10")] == (3.0, 1.0, 0.0)  # 2 h local + 1 h before midnight
    assert daily[("Kharkivska oblast", "2024-01-11")] == (3.0, 3.0, 0.75)
    # Kyiv City: the whole 4 h alert falls on 31 March Kyiv time (it starts 00:30 local), none on the 30th.
    assert daily[("Kyiv City", "2024-03-30")][0] == 0.0
    assert daily[("Kyiv City", "2024-03-31")][0] == 4.0
    assert daily[("Kyiv City", "2024-01-10")][2] == 1.0  # volunteer overlap merged: 10:00-11:00


def test_no_data_is_null_not_zero(wh):
    # The official data covers 10 Jan .. 31 Mar 2024 (Kyiv days), volunteer data 10 .. 11 Jan 2024.
    before = q(wh, "select any_alert_hours, volunteer_alert_hours from agg_region_daily where region = 'Kyiv City' and day = '2023-12-31'")
    inside_official_only = q(wh, "select any_alert_hours, volunteer_alert_hours from agg_region_daily where region = 'Kyiv City' and day = '2024-02-15'")
    assert before == [(None, None)]
    assert inside_official_only == [(0.0, None)]
    assert q(wh, "select count(distinct region) from agg_region_daily") == [(25,)]


def test_monthly_totals_and_coverage_days(wh):
    rows = q(wh, """select any_alert_hours, oblastwide_alert_hours, official_days, volunteer_days, days_in_month
                    from agg_region_monthly where region = 'Lvivska oblast' and month = '2024-02-01'""")
    assert rows == [(193.0, 193.0, 29, 0, 29)]  # 8 days 1 h = 193 h, all inside February


def test_start_hour_heatmap_uses_kyiv_time(wh):
    # Kharkiv's 10:00 UTC episode started at 12:00 Kyiv time on a Wednesday (ISO weekday 3).
    rows = q(wh, """select weekday, hour, episodes from agg_start_hour_weekday
                    where metric = 'official_any' and iso_code = 'UA-63' order by weekday, hour""")
    assert rows == [(3, 12, 1), (3, 23, 1)]


def test_incremental_rerun_equals_full_refresh(wh, tmp_path):
    work = tmp_path / "dbt"
    snapshot = lambda: q(wh, "select count(*), sum(hash(alert_id)) from fct_alerts")  # noqa: E731
    first = snapshot()
    assert run_dbt(["build", "--select", "fct_alerts"], warehouse=wh, work_dir=work).success  # incremental run
    assert snapshot() == first
    assert run_dbt(["build", "--select", "fct_alerts", "--full-refresh"], warehouse=wh, work_dir=work).success
    assert snapshot() == first
