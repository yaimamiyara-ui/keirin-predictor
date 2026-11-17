import os
from pathlib import Path
import pytest
from keirin.fetch.netkeirin import parse_nk_entry, parse_nk_comments
from keirin.fetch.jma import parse_jma_10min, parse_jma_stations, parse_jma_prec_list
from keirin.fetch.openmeteo import parse_openmeteo

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    if not path.exists():
        pytest.skip(f"Fixture file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_nk_entry_parsing():
    if (FIXTURES_DIR / "netkeirin_entry_202611174612.html").exists():
        html = load_fixture("netkeirin_entry_202611174612.html")
    else:
        html = load_fixture("nk_entry_ex.html")

    res = parse_nk_entry(html, "202611174612")
    assert res["race_id"] == "202611174612"

    if res["lineup"]:
        assert res["lineup"] == "1-6-5/4/3-2-7"
    else:
        assert res["lineup_order"] == "1,6,5,4,3,2,7"

    assert len(res["entries"]) > 0
    car1 = next(e for e in res["entries"] if e["car_num"] == 1)
    assert "村田祐" in car1["name"]
    assert car1["score"] == 103.59
    assert car1["style"] == "逃"
    assert car1["gear"] == 3.93
    assert car1["comment"] == "部分部分でいいところもあるし、それらを繋げられたら。"


def test_nk_entry_2016_parsing():
    html = load_fixture("nk_entry2016_ex.html")
    res = parse_nk_entry(html, "201601010001")
    assert res["lineup"] is not None or res["lineup_order"] is not None


def test_jma_10min_parsing():
    if (FIXTURES_DIR / "jma_10min_47607_2026-03-10.html").exists():
        html = load_fixture("jma_10min_47607_2026-03-10.html")
    else:
        html = load_fixture("jma_ex.html")

    data = parse_jma_10min(html, kind="s", date_str="2026-03-10")
    assert len(data) > 0
    first_row = next(r for r in data if r["time"] == "00:10")
    assert first_row["wind_speed"] == 2.0
    assert first_row["wind_dir"] == "東"


def test_jma_prec_and_stations():
    pref_txt = load_fixture("jma_pref00_ex.txt")
    precs = parse_jma_prec_list(pref_txt)
    assert 11 in precs
    assert 15 in precs

    stn_txt = load_fixture("jma_pref55_ex.txt")
    stations = parse_jma_stations(stn_txt, 55)
    assert len(stations) > 0
    uozu = next(s for s in stations if s["name"] == "魚津")
    assert uozu["block_no"] == "0547"
    assert uozu["has_wind"] is True
    assert uozu["end_date"] is None


def test_parse_openmeteo():
    sample_json = {
        "hourly": {
            "time": ["2026-03-10T00:00", "2026-03-10T01:00"],
            "wind_speed_10m": [2.0, 1.8],
            "wind_direction_10m": [90, 95],
            "temperature_2m": [4.3, 4.2],
            "precipitation": [0.0, 0.0],
        }
    }
    res = parse_openmeteo(sample_json)
    assert len(res) == 2
    assert res[0]["time"] == "2026-03-10T00:00"
    assert res[0]["wind_speed"] == 2.0
    assert res[0]["wind_dir_deg"] == 90
    assert res[0]["temp"] == 4.3
    assert res[0]["precip"] == 0.0
