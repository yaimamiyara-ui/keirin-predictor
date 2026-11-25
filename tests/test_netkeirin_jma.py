from pathlib import Path
import pytest
from keirin.fetch.netkeirin import parse_nk_entry, parse_nk_comments
from keirin.fetch.jma import parse_jma_10min, parse_jma_stations, parse_jma_prec_list
from keirin.fetch.openmeteo import parse_openmeteo

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_FIXTURES_DIR = Path(__file__).parent / "fixtures_synthetic"


def is_using_real_fixture(filename: str) -> bool:
    return (FIXTURES_DIR / filename).exists()


def read_fixture(filename: str) -> str:
    path = FIXTURES_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    synthetic_path = SYNTHETIC_FIXTURES_DIR / f"synthetic_{filename}"
    if synthetic_path.exists():
        return synthetic_path.read_text(encoding="utf-8")
    pytest.skip(f"Fixture file not found: {path} or {synthetic_path}")


load_fixture = read_fixture


def test_nk_entry_parsing():
    if is_using_real_fixture("netkeirin_entry_202611174612.html"):
        real = True
        html = read_fixture("netkeirin_entry_202611174612.html")
    else:
        real = is_using_real_fixture("nk_entry_ex.html")
        html = read_fixture("nk_entry_ex.html")

    res = parse_nk_entry(html, "202611174612")
    assert res["race_id"] == "202611174612"

    if res["lineup"]:
        assert res["lineup"] == "1-6-5/4/3-2-7"
    else:
        assert res["lineup_order"] == "1,6,5,4,3,2,7"

    assert len(res["entries"]) > 0
    car1 = next(e for e in res["entries"] if e["car_num"] == 1)
    if real:
        assert "村田祐" in car1["name"]
        assert car1["score"] == 103.59
        assert car1["style"] == "逃"
        assert car1["gear"] == 3.93
        assert car1["comment"] == "部分部分でいいところもあるし、それらを繋げられたら。"
    else:
        assert car1["name"] == "テスト選手A"
        assert car1["score"] == 95.10
        assert car1["style"] == "逃"
        assert car1["gear"] == 3.92
        assert car1["comment"] == "自力でテスト走行。"


def test_nk_entry_2016_parsing():
    html = read_fixture("nk_entry2016_ex.html")
    res = parse_nk_entry(html, "201601010001")
    assert res["lineup"] is not None or res["lineup_order"] is not None


def test_jma_10min_parsing():
    if is_using_real_fixture("jma_10min_47607_2026-03-10.html"):
        real = True
        html = read_fixture("jma_10min_47607_2026-03-10.html")
    else:
        real = is_using_real_fixture("jma_ex.html")
        html = read_fixture("jma_ex.html")

    data = parse_jma_10min(html, kind="s", date_str="2026-03-10")
    assert len(data) > 0
    first_row = next(r for r in data if r["time"] == "00:10")
    if real:
        assert first_row["wind_speed"] == 2.0
        assert first_row["wind_dir"] == "東"
    else:
        assert first_row["wind_speed"] == 1.5
        assert first_row["wind_dir"] == "北"


def test_jma_prec_and_stations():
    pref_txt = read_fixture("jma_pref00_ex.txt")
    precs = parse_jma_prec_list(pref_txt)
    assert 11 in precs
    assert 15 in precs

    stn_txt = read_fixture("jma_pref55_ex.txt")
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
