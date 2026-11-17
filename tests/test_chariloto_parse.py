import json
from pathlib import Path
import pytest

from keirin.fetch.chariloto import (
    parse_venue_year_days,
    parse_calendar,
    parse_vel_menu,
    parse_race_info,
    parse_odds,
    parse_lineup,
    parse_results_day,
    parse_velinfo,
    parse_athlete_profile
)
from keirin.common.schema import (
    RaceRow, EntryRow, PredictionRow, OddsRow, LineupRow,
    ResultRow, PayoutRow, VenueInfoRow, CalendarRow, AthleteProfileRow
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def read_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    if not path.exists():
        pytest.skip(f"Fixture file {filename} does not exist at {path}")
    return path.read_text(encoding="utf-8")


def test_parse_venue_year_days():
    html = read_fixture("results_year_13_2016.html")
    days = parse_venue_year_days(html, vel="13", year=2016)
    assert isinstance(days, list)
    assert "2016-01-03" in days
    assert "2016-01-02" in days
    assert "2016-01-04" in days
    assert all(d.startswith("2016-") for d in days)
    assert days == sorted(days)


def test_parse_calendar():
    html = read_fixture("calendar_2026_11.html")
    items = parse_calendar(html, year=2026, month=11)
    assert isinstance(items, list)
    assert len(items) > 0
    
    validated = [CalendarRow(**item) for item in items]
    assert len(validated) > 0
    
    aomori = [x for x in items if x["venue_name"] == "青森" and x["day"] == 3]
    assert len(aomori) == 1
    assert aomori[0]["grade"] == "F1"


def test_parse_vel_menu():
    raw_json = read_fixture("vel_menu_2026-11-17_46.json")
    data = json.loads(raw_json)
    meeting, races = parse_vel_menu(data)
    
    assert meeting["vel_name"] == "富山"
    assert meeting["open_day"] == "2026-11-17"
    assert len(races) == 12
    assert races[0]["race_num"] == 1


def test_parse_race_info():
    raw_json = read_fixture("race_info_2026-03-10_13_1.json")
    data = json.loads(raw_json)
    race, entries, prediction = parse_race_info(data)
    
    race_row = RaceRow(**race)
    assert race_row.vel_code == "13"
    assert race_row.open_day == "2026-03-10"
    assert race_row.race_num == 1
    assert race_row.class_name == "A級"
    assert race_row.event_name == "一般"
    
    entry_rows = [EntryRow(**e) for e in entries]
    assert len(entry_rows) == 7
    
    # 7番 山崎航 の検証
    yama = next(e for e in entry_rows if e.entry_num == 7)
    assert yama.name == "山崎航"
    assert yama.average_point == pytest.approx(88.458, rel=1e-2)
    assert yama.average_win_raw == 250
    assert yama.history is not None
    
    pred_row = PredictionRow(**prediction)
    assert pred_row.marks is not None
    assert "7:◎" in pred_row.marks


def test_parse_odds_final():
    raw_json = read_fixture("odds_final_2016-01-03_13_1.json")
    data = json.loads(raw_json)
    odds_list = parse_odds(data, snapshot_label="final")
    
    odds_rows = [OddsRow(**o) for o in odds_list]
    assert len(odds_rows) > 0
    
    wide_12 = next(o for o in odds_rows if o.bet_type == "wide" and o.combo == "1-2")
    assert wide_12.odds_min == 3.8
    assert wide_12.odds_max == 4.5


def test_parse_odds_live():
    raw_json = read_fixture("odds_live_2026-11-17_46_12.json")
    data = json.loads(raw_json)
    odds_list = parse_odds(data, snapshot_label="live")
    
    odds_rows = [OddsRow(**o) for o in odds_list]
    assert len(odds_rows) > 0
    
    wide_12 = next(o for o in odds_rows if o.bet_type == "wide" and o.combo == "1-2")
    assert wide_12.odds_min == 3.9
    assert wide_12.odds_max == 4.3


def test_parse_lineup():
    html = read_fixture("athletes_2026-11-17_46_12.html")
    lineup_info = parse_lineup(html)
    
    lineup_row = LineupRow(**lineup_info)
    assert lineup_row.lineup == "1-6-5/4/3-2-7"
    assert lineup_row.lines == [[1, 6, 5], [4], [3, 2, 7]]
    assert lineup_row.line_count == 3


def test_parse_results_day():
    html = read_fixture("results_day_13_2026-03-10.html")
    results, payouts, lineups = parse_results_day(html, vel="13", date="2026-03-10")
    
    res_rows = [ResultRow(**r) for r in results]
    pay_rows = [PayoutRow(**p) for p in payouts]
    line_rows = [LineupRow(**l) for l in lineups]
    
    assert len(res_rows) > 0
    assert len(pay_rows) > 0
    assert len(line_rows) > 0
    
    # 1R 1着 山崎航 捲くり
    r1_1st = next(r for r in res_rows if r.race_num == 1 and r.rank == 1)
    assert r1_1st.car_num == 7
    assert r1_1st.name == "山崎航"
    assert r1_1st.factor == "捲くり"
    
    # 3連単 7-3-4 16,790円
    sanrentan = next(p for p in pay_rows if p.race_num == 1 and p.bet_type == "sanrentan" and p.combo == "7-3-4")
    assert sanrentan.payout_yen == 16790
    
    # ワイド 3=7 1,500円
    wide_37 = next(p for p in pay_rows if p.race_num == 1 and p.bet_type == "wide" and p.combo == "3=7")
    assert wide_37.payout_yen == 1500
    
    # 周回予想 7-1-5-6/2-4/3
    l1 = next(l for l in line_rows if l.race_num == 1)
    assert l1.lineup == "7-1-5-6/2-4/3"


def test_parse_velinfo():
    html = read_fixture("velinfo_46.html")
    info = parse_velinfo(html, vel="46")
    
    row = VenueInfoRow(**info)
    assert row.bank_length_m == 333
    assert row.straight_m == 43.0
    assert row.strike_first_nige == 27
    assert row.strike_first_makuri == 35
    assert row.strike_first_sashi == 38


def test_parse_athlete_profile():
    html = read_fixture("athlete_profile_15287.html")
    profile = parse_athlete_profile(html)
    
    row = AthleteProfileRow(**profile)
    assert row.registration_num == "015287" or row.registration_num == "15287"
    assert row.height_cm == 170
    assert row.weight_kg == 74
    assert row.home_bank == "高知"
