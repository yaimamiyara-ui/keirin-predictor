from datetime import date
import json
import pathlib
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from keirin.storage import ParquetStorage, StorageError, normalize_weather_frame
from keirin.scripts.migrate_weather_times import migrate_weather_dir, main as migrate_weather_main
from keirin.fetch.http import PoliteClient, PoliteError, PoliteRateLimitError
from keirin.fetch.client import ChariClient, ChariError, ChariRateLimitError
from keirin.fetch.chariloto import parse_results_day, parse_vel_menu, parse_race_info, parse_odds
from keirin.fetch.netkeirin import parse_nk_entry
import keirin.scripts.collect as collect_module

SYNTHETIC_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures_synthetic"


def test_parquet_storage_save_and_done(tmp_path: pathlib.Path):
    storage = ParquetStorage(data_dir=tmp_path)
    open_day = "2024-05-10"
    vel_code = "01"

    records = [
        {"vel_code": vel_code, "open_day": open_day, "race_num": 1, "val": "A"},
        {"vel_code": vel_code, "open_day": open_day, "race_num": 2, "val": "B"},
    ]
    storage.save("race", open_day, records)

    p_path = tmp_path / "race" / "2024" / "2024-05.parquet"
    assert p_path.exists()

    df = pd.read_parquet(p_path)
    assert len(df) == 2

    # 重複保存で置換テスト
    records_updated = [
        {"vel_code": vel_code, "open_day": open_day, "race_num": 1, "val": "A_updated"},
        {"vel_code": vel_code, "open_day": open_day, "race_num": 3, "val": "C"},
    ]
    storage.save("race", open_day, records_updated)

    df_updated = pd.read_parquet(p_path)
    assert len(df_updated) == 3
    r1 = df_updated[df_updated["race_num"] == 1].iloc[0]
    assert r1["val"] == "A_updated"

    # doneテスト
    assert not storage.is_done(vel_code, open_day, "chariloto")
    storage.add_done(vel_code, open_day, "chariloto")
    assert storage.is_done(vel_code, open_day, "chariloto")

    done_file = tmp_path / "_done" / "2024" / "2024-05.txt"
    assert done_file.exists()


def test_collect_script_normal_and_partial_failure(tmp_path: pathlib.Path, monkeypatch):
    data_dir = tmp_path / "data"
    venues_dir = tmp_path / "config"
    venues_dir.mkdir(parents=True, exist_ok=True)

    venues_config = {
        "01": {
            "vel_code": "01",
            "name": "函館",
            "is_indoor": False,
            "lat": 41.791,
            "lon": 140.768,
            "jma": {"kind": "a", "block_no": "1234", "prec_no": 11}
        }
    }
    with open(venues_dir / "venues.json", "w", encoding="utf-8") as f:
        json.dump(venues_config, f)

    # モックデータ
    def mock_fetch_calendar(client, yr, mo):
        return [{"venue_name": "函館", "day": 10, "grade": "F1", "flags": []}]

    def mock_fetch_venue_year_days(client, vel, yr):
        return ["2024-05-10"]

    def mock_fetch_results_day(client, vel, date):
        res = [
            {"open_day": date, "vel_code": vel, "race_num": 1, "car_num": 1, "rank": 1},
            {"open_day": date, "vel_code": vel, "race_num": 2, "car_num": 1, "rank": 1},
        ]
        payouts = [
            {"open_day": date, "vel_code": vel, "race_num": 1, "bet_type": "nirentan", "combo": "1-2", "payout_yen": 500},
            {"open_day": date, "vel_code": vel, "race_num": 2, "bet_type": "nirentan", "combo": "1-2", "payout_yen": 600},
        ]
        lineups = [
            {"open_day": date, "vel_code": vel, "race_num": 1, "lineup": "1-2-3", "line_count": 1},
            {"open_day": date, "vel_code": vel, "race_num": 2, "lineup": "1-2-3", "line_count": 1},
        ]
        return res, payouts, lineups

    def mock_fetch_vel_menu(client, vel, date):
        return {"vel_name": "函館", "open_day": date}, [{"race_num": 1}, {"race_num": 2}]

    def mock_fetch_race_info(client, open_day, vel, r_num):
        race = {"open_day": open_day, "vel_code": vel, "race_num": r_num}
        entries = [{"entry_num": 1, "name": "選手1"}, {"entry_num": 2, "name": "選手2"}]
        pred = {"marks": "1:◎", "sequences": "1-2-3", "comment": "テスト"}
        return race, entries, pred

    def mock_fetch_odds(client, open_day, vel, r_num, snapshot_label=""):
        return [{"bet_type": "nirentan", "combo": "1-2", "odds_min": 2.5, "odds_max": 2.5, "snapshot": snapshot_label}]

    def mock_fetch_nk_entry(client, race_id):
        return {
            "race_id": race_id,
            "lineup": "1-2-3",
            "entries": [{"car_num": 1, "name": "選手1"}],
            "honshi": {"opinion": "本命視", "bets": ["1-2"]}
        }

    def mock_fetch_nk_comments(client, race_id):
        return [{"car_num": 1, "name": "選手1", "comment": "調子良い"}]

    def mock_fetch_jma_10min(client, station, date_str):
        return [{"time": "10:00", "temp": 20.0, "wind_speed": 2.5}]

    monkeypatch.setattr(collect_module, "fetch_calendar", mock_fetch_calendar)
    monkeypatch.setattr(collect_module, "fetch_venue_year_days", mock_fetch_venue_year_days)
    monkeypatch.setattr(collect_module, "fetch_results_day", mock_fetch_results_day)
    monkeypatch.setattr(collect_module, "fetch_vel_menu", mock_fetch_vel_menu)
    monkeypatch.setattr(collect_module, "fetch_race_info", mock_fetch_race_info)
    monkeypatch.setattr(collect_module, "fetch_odds", mock_fetch_odds)
    monkeypatch.setattr(collect_module, "fetch_nk_entry", mock_fetch_nk_entry)
    monkeypatch.setattr(collect_module, "fetch_nk_comments", mock_fetch_nk_comments)
    monkeypatch.setattr(collect_module, "fetch_jma_10min", mock_fetch_jma_10min)

    # 正常実行テスト
    monkeypatch.setattr("sys.argv", [
        "collect.py", "--start", "2024-05-10", "--end", "2024-05-10",
        "--venues", "01", "--data-dir", str(data_dir)
    ])
    
    if True:  # Path.exists を全体で差し替えると保存処理が壊れるため外した（Claude修正）
        with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
            collect_module.main()

    storage = ParquetStorage(data_dir=data_dir)
    assert storage.is_done("01", "2024-05-10", "chariloto")
    assert storage.is_done("01", "2024-05-10", "netkeirin")
    assert storage.is_done("01", "2024-05-10", "weather")

    done_file = data_dir / "_done" / "2024" / "2024-05.txt"
    with open(done_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 3
    assert "01|2024-05-10|chariloto" in lines
    assert "01|2024-05-10|netkeirin" in lines
    assert "01|2024-05-10|weather" in lines

    # netkeirin 失敗時のテスト
    data_dir_fail = tmp_path / "data_fail"
    def mock_fetch_nk_entry_fail(client, race_id):
        raise RuntimeError("Netkeirin connection error")

    monkeypatch.setattr(collect_module, "fetch_nk_entry", mock_fetch_nk_entry_fail)
    monkeypatch.setattr("sys.argv", [
        "collect.py", "--start", "2024-05-10", "--end", "2024-05-10",
        "--venues", "01", "--data-dir", str(data_dir_fail)
    ])

    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    storage_fail = ParquetStorage(data_dir=data_dir_fail)
    assert storage_fail.is_done("01", "2024-05-10", "chariloto")
    assert not storage_fail.is_done("01", "2024-05-10", "netkeirin")
    assert storage_fail.is_done("01", "2024-05-10", "weather")

    done_file_fail = data_dir_fail / "_done" / "2024" / "2024-05.txt"
    with open(done_file_fail, "r", encoding="utf-8") as f:
        lines_fail = [line.strip() for line in f if line.strip()]
    assert len(lines_fail) == 2
    assert "01|2024-05-10|chariloto" in lines_fail
    assert "01|2024-05-10|netkeirin" not in lines_fail
    assert "01|2024-05-10|weather" in lines_fail


def test_parquet_storage_corrupted_file_kept(tmp_path: pathlib.Path):
    storage = ParquetStorage(data_dir=tmp_path)
    open_day = "2024-05-10"
    p_path = tmp_path / "race" / "2024" / "2024-05.parquet"
    p_path.parent.mkdir(parents=True, exist_ok=True)
    corrupted_bytes = b"not a valid parquet file"
    p_path.write_bytes(corrupted_bytes)

    with pytest.raises(StorageError):
        storage.save("race", open_day, [{"vel_code": "01", "open_day": open_day, "race_num": 1}])

    # Broken parquet must be preserved, not overwritten with new rows only
    assert p_path.read_bytes() == corrupted_bytes

    # Subsequent writes or done markers for that month must be refused
    storage.add_done("01", open_day, "chariloto")
    assert not storage.is_done("01", open_day, "chariloto")


def test_collect_empty_parse_and_weather_and_month_complete(tmp_path: pathlib.Path, monkeypatch):
    venues_config = {
        "01": {
            "vel_code": "01",
            "name": "函館",
            "is_indoor": False,
            "lat": 41.791,
            "lon": 140.768,
            "jma": {"kind": "a", "block_no": "1234", "prec_no": 11},
        }
    }

    monkeypatch.setattr(
        collect_module,
        "fetch_calendar",
        lambda client, yr, mo: [{"venue_name": "函館", "day": 10, "grade": "F1", "flags": []}],
    )
    monkeypatch.setattr(collect_module, "fetch_venue_year_days", lambda client, vel, yr: ["2024-05-10"])
    monkeypatch.setattr(
        collect_module,
        "fetch_race_info",
        lambda client, open_day, vel, r_num: (
            {"open_day": open_day, "vel_code": vel, "race_num": r_num},
            [{"entry_num": 1, "name": "選手1"}],
            {"marks": "1:◎", "sequences": "1-2", "comment": "ok"},
        ),
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_odds",
        lambda client, open_day, vel, r_num, snapshot_label="": [
            {"bet_type": "nirentan", "combo": "1-2", "odds_min": 2.0, "odds_max": 2.0, "snapshot": snapshot_label}
        ],
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_nk_entry",
        lambda client, race_id: {
            "race_id": race_id,
            "lineup": "1-2",
            "entries": [{"car_num": 1, "name": "選手1"}],
            "honshi": {"opinion": "ok", "bets": ["1-2"]},
        },
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_nk_comments",
        lambda client, race_id: [{"car_num": 1, "name": "選手1", "comment": "好調"}],
    )

    # 1) Empty Chariloto results and empty Netkeirin vel_menu when races were expected -> must NOT mark done
    data_dir_empty = tmp_path / "data_empty"
    monkeypatch.setattr(collect_module, "fetch_results_day", lambda client, vel, date: ([], [], []))
    monkeypatch.setattr(
        collect_module,
        "fetch_vel_menu",
        lambda client, vel, date: ({"vel_name": "函館", "open_day": date}, []),
    )
    monkeypatch.setattr(collect_module, "fetch_jma_10min", lambda client, station, date_str: [])
    monkeypatch.setattr(collect_module, "fetch_openmeteo_hourly", lambda client, lat, lon, s, e: [])

    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2024-05-01", "--end", "2024-05-31", "--venues", "01", "--data-dir", str(data_dir_empty)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st_empty = ParquetStorage(data_dir=data_dir_empty)
    assert not st_empty.is_done("01", "2024-05-10", "chariloto")
    assert not st_empty.is_done("01", "2024-05-10", "netkeirin")
    assert not st_empty.is_done("01", "2024-05-10", "weather")
    assert not st_empty.is_done("*", "2024-05-01", "month_complete")

    # 2) Empty JMA falls back to Open-Meteo; when all sources succeed for full month, month_complete is marked
    data_dir_fb = tmp_path / "data_fb"
    monkeypatch.setattr(
        collect_module,
        "fetch_results_day",
        lambda client, vel, date: (
            [{"open_day": date, "vel_code": vel, "race_num": 1, "car_num": 1, "rank": 1}],
            [{"open_day": date, "vel_code": vel, "race_num": 1, "bet_type": "nirentan", "combo": "1-2", "payout_yen": 500}],
            [{"open_day": date, "vel_code": vel, "race_num": 1, "lineup": "1-2", "line_count": 1}],
        ),
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_vel_menu",
        lambda client, vel, date: ({"vel_name": "函館", "open_day": date}, [{"race_num": 1}]),
    )
    monkeypatch.setattr(collect_module, "fetch_jma_10min", lambda client, station, date_str: [])
    monkeypatch.setattr(
        collect_module,
        "fetch_openmeteo_hourly",
        lambda client, lat, lon, s, e: [{"time": f"{s}T10:00", "temp": 18.5, "wind_speed": 3.0}],
    )

    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2024-05-01", "--end", "2024-05-31", "--venues", "01", "--data-dir", str(data_dir_fb)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st_fb = ParquetStorage(data_dir=data_dir_fb)
    assert st_fb.is_done("01", "2024-05-10", "chariloto")
    assert st_fb.is_done("01", "2024-05-10", "netkeirin")
    assert st_fb.is_done("01", "2024-05-10", "weather")
    assert st_fb.is_done("*", "2024-05-01", "month_complete")

    w_df = pd.read_parquet(data_dir_fb / "weather" / "2024" / "2024-05.parquet")
    assert len(w_df) == 1
    assert w_df.iloc[0]["source"] == "openmeteo"
    assert w_df.iloc[0]["time"] == "2024-05-10T10:00"


def test_http_and_chari_429_retry_after_and_deadline(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    now_mono = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now_mono[0])

    # 1) Retry-After honored and capped at max_retry_after
    client = PoliteClient(min_interval=3.0, deadline=500.0, max_retry_after=30.0)
    resp_429 = MagicMock(status_code=429, headers={"Retry-After": "120"})
    resp_200 = MagicMock(status_code=200, text="ok")
    client.session.request = MagicMock(side_effect=[resp_429, resp_200])

    assert client.get_html("https://example.com/test") == "ok"
    assert 30.0 in sleeps

    # 2) Retry-After exceeding remaining time before deadline immediately aborts and skips source
    chari = ChariClient(min_interval=3.0, deadline=110.0, max_retry_after=60.0)
    resp_429_long = MagicMock(status_code=429, headers={"Retry-After": "25"})
    chari.session.request = MagicMock(return_value=resp_429_long)

    with pytest.raises(ChariRateLimitError):
        chari.get_html("/api/test")

    assert chari.rate_limited is True
    assert chari.session.request.call_count == 1

    # Subsequent calls fail fast without making network requests
    with pytest.raises(ChariRateLimitError):
        chari.get_html("/api/test2")
    assert chari.session.request.call_count == 1


def test_collect_2race_day_partial_chariloto_and_netkeirin(tmp_path: pathlib.Path, monkeypatch):
    venues_config = {
        "13": {
            "vel_code": "13",
            "name": "テスト場",
            "is_indoor": True,
        }
    }

    res_html = (SYNTHETIC_FIXTURES_DIR / "synthetic_results_day_13_2026-03-10.html").read_text(encoding="utf-8")
    r1_results, r1_payouts, r1_lineups = parse_results_day(res_html, vel="13", date="2026-03-10")

    race_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_race_info_2026-03-10_13_1.json").read_text(encoding="utf-8"))
    r1_info, r1_entries, r1_pred = parse_race_info(race_json)

    odds_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_odds_final_2016-01-03_13_1.json").read_text(encoding="utf-8"))
    r1_odds = parse_odds(odds_json, snapshot_label="final")

    nk_html = (SYNTHETIC_FIXTURES_DIR / "synthetic_nk_entry_ex.html").read_text(encoding="utf-8")
    nk_parsed = parse_nk_entry(nk_html, "202603101301")

    monkeypatch.setattr(
        collect_module,
        "fetch_calendar",
        lambda client, yr, mo: [{"venue_name": "テスト場", "day": 10, "grade": "F2", "flags": []}],
    )
    monkeypatch.setattr(collect_module, "fetch_venue_year_days", lambda client, vel, yr: ["2026-03-10"])

    # 2-race meeting menu from synthetic vel_menu fixture (trimmed to 2 races)
    vel_menu_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_vel_menu_2026-11-17_46.json").read_text(encoding="utf-8"))
    meeting_base, races_base = parse_vel_menu(vel_menu_json)
    meeting_2r = dict(meeting_base, vel_name="テスト場", open_day="2026-03-10")
    races_2r = [r for r in races_base if r["race_num"] in (1, 2)]

    monkeypatch.setattr(collect_module, "fetch_vel_menu", lambda client, vel, date: (meeting_2r, races_2r))
    monkeypatch.setattr(
        collect_module,
        "fetch_race_info",
        lambda client, open_day, vel, r_num: (
            dict(r1_info, open_day=open_day, vel_code=vel, race_num=r_num),
            [dict(e) for e in r1_entries],
            dict(r1_pred),
        ),
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_odds",
        lambda client, open_day, vel, r_num, snapshot_label="": [dict(o, snapshot=snapshot_label) for o in r1_odds],
    )
    monkeypatch.setattr(collect_module, "fetch_nk_comments", lambda client, race_id: [])

    # Case 1: 2-race day where Chariloto results only have race 1 (race 2 empty/missing)
    # and Netkeirin has race 1 entries but race 2 entries are empty.
    monkeypatch.setattr(
        collect_module,
        "fetch_results_day",
        lambda client, vel, date: ([dict(r) for r in r1_results], [dict(p) for p in r1_payouts], [dict(l) for l in r1_lineups]),
    )

    def mock_nk_entry_partial(client, race_id):
        if race_id.endswith("01"):
            return {
                "race_id": race_id,
                "lineup": nk_parsed["lineup"],
                "entries": [dict(e) for e in nk_parsed["entries"]],
                "honshi": nk_parsed["honshi"],
            }
        return {"race_id": race_id, "lineup": None, "entries": [], "honshi": None}

    monkeypatch.setattr(collect_module, "fetch_nk_entry", mock_nk_entry_partial)

    data_dir_partial = tmp_path / "data_partial"
    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2026-03-10", "--end", "2026-03-10", "--venues", "13", "--data-dir", str(data_dir_partial)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st_partial = ParquetStorage(data_dir=data_dir_partial)
    assert not st_partial.is_done("13", "2026-03-10", "chariloto")
    assert not st_partial.is_done("13", "2026-03-10", "netkeirin")
    assert st_partial.is_done("13", "2026-03-10", "weather")

    # Case 2: Race 2 is explicitly cancelled in vel_menu -> both Chariloto and Netkeirin succeed with Race 1 only
    races_2r_cancelled = [
        races_2r[0],
        dict(races_2r[1], event_name="開催中止"),
    ]
    monkeypatch.setattr(collect_module, "fetch_vel_menu", lambda client, vel, date: (meeting_2r, races_2r_cancelled))

    data_dir_cancelled = tmp_path / "data_cancelled"
    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2026-03-10", "--end", "2026-03-10", "--venues", "13", "--data-dir", str(data_dir_cancelled)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st_cancelled = ParquetStorage(data_dir=data_dir_cancelled)
    assert st_cancelled.is_done("13", "2026-03-10", "chariloto")
    assert st_cancelled.is_done("13", "2026-03-10", "netkeirin")


def test_edge_month_completion_and_select_backfill_month(tmp_path: pathlib.Path, monkeypatch):
    # 1) Edge month completion in collect.py when configured range ends mid-month (2026-11-16)
    venues_config = {
        "13": {
            "vel_code": "13",
            "name": "テスト場",
            "is_indoor": True,
        }
    }
    range_file = tmp_path / "backfill_range.txt"
    range_file.write_text("2025-01-01,2026-11-16\n", encoding="utf-8")

    # Venue has scheduled days on 2026-11-10 (inside range) and 2026-11-20 (outside range)
    monkeypatch.setattr(
        collect_module,
        "fetch_calendar",
        lambda client, yr, mo: [{"venue_name": "テスト場", "day": 10, "grade": "F2", "flags": []}],
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_venue_year_days",
        lambda client, vel, yr: ["2026-11-10", "2026-11-20"],
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_vel_menu",
        lambda client, vel, d: ({"vel_name": "テスト場", "open_day": d}, [{"race_num": 1}]),
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_results_day",
        lambda client, vel, d: (
            [{"open_day": d, "vel_code": vel, "race_num": 1, "car_num": 1, "rank": 1}],
            [{"open_day": d, "vel_code": vel, "race_num": 1, "bet_type": "nirentan", "combo": "1-2", "payout_yen": 500}],
            [{"open_day": d, "vel_code": vel, "race_num": 1, "lineup": "1-2", "line_count": 1}],
        ),
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_race_info",
        lambda client, open_day, vel, r_num: (
            {"open_day": open_day, "vel_code": vel, "race_num": r_num},
            [{"entry_num": 1, "name": "テスト選手A"}],
            {"marks": "1:◎", "sequences": "1-2", "comment": "ok"},
        ),
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_odds",
        lambda client, open_day, vel, r_num, snapshot_label="": [
            {"bet_type": "nirentan", "combo": "1-2", "odds_min": 2.0, "odds_max": 2.0, "snapshot": snapshot_label}
        ],
    )
    monkeypatch.setattr(
        collect_module,
        "fetch_nk_entry",
        lambda client, race_id: {
            "race_id": race_id,
            "lineup": "1-2",
            "entries": [{"car_num": 1, "name": "テスト選手A"}],
            "honshi": {"opinion": "ok", "bets": ["1-2"]},
        },
    )
    monkeypatch.setattr(collect_module, "fetch_nk_comments", lambda client, race_id: [])

    data_dir = tmp_path / "data"
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "backfill_range.txt").write_text("2025-01-01,2026-11-16\n", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2026-11-01", "--end", "2026-11-16", "--venues", "13", "--data-dir", str(data_dir)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st = ParquetStorage(data_dir=data_dir)
    assert st.is_done("13", "2026-11-10", "chariloto")
    assert not st.is_done("13", "2026-11-20", "chariloto")
    assert st.is_done("*", "2026-11-01", "month_complete")

    # 2) select_backfill_month marker selection, deferred vs complete
    done_dir = tmp_path / "done_test"
    r_file = tmp_path / "range_short.txt"
    r_file.write_text("2026-10-15,2026-11-16\n", encoding="utf-8")

    # When 2026-10 is not complete and jst_today is 2026-11-25 (daily_from = 2026-11-01):
    sel1 = collect_module.select_backfill_month(range_file=r_file, done_dir=done_dir, jst_today=date(2026, 11, 25))
    assert sel1["status"] == "selected"
    assert sel1["ym"] == "2026-10"
    assert sel1["start"] == "2026-10-15"
    assert sel1["end"] == "2026-10-31"
    assert sel1["done_inc"] == "_done/2026/2026-10*.txt"
    assert sel1["parquet_inc"] == "*/2026/2026-10.parquet"

    # Mark 2026-10 complete; 2026-11 is still incomplete, but owned by daily-fetch on 2026-11-25 -> deferred
    oct_done = done_dir / "2026" / "2026-10.txt"
    oct_done.parent.mkdir(parents=True, exist_ok=True)
    oct_done.write_text("*|2026-10-01|month_complete\n", encoding="utf-8")

    sel2 = collect_module.select_backfill_month(range_file=r_file, done_dir=done_dir, jst_today=date(2026, 11, 25))
    assert sel2["status"] == "deferred"
    assert sel2["done"] == "1"
    assert sel2["deferred"] == "1"
    assert sel2["ym"] == "2026-11"

    # Once jst_today moves to 2026-12-02 (daily_from = 2026-12-01), 2026-11 is selected with edge end 2026-11-16
    sel3 = collect_module.select_backfill_month(range_file=r_file, done_dir=done_dir, jst_today=date(2026, 12, 2))
    assert sel3["status"] == "selected"
    assert sel3["ym"] == "2026-11"
    assert sel3["start"] == "2026-11-01"
    assert sel3["end"] == "2026-11-16"
    assert sel3["done_inc"] == "_done/2026/2026-11*.txt"

    # Mark 2026-11 complete -> status is complete (even if jst_today was still in Nov 2026)
    nov_done = done_dir / "2026" / "2026-11.txt"
    nov_done.write_text("*|2026-11-01|month_complete\n", encoding="utf-8")

    sel4 = collect_module.select_backfill_month(range_file=r_file, done_dir=done_dir, jst_today=date(2026, 11, 25))
    assert sel4["status"] == "complete"
    assert sel4["done"] == "1"
    assert sel4["deferred"] == "0"


def test_jma_time_range_and_migration_scope(tmp_path: pathlib.Path):
    from keirin.fetch.jma import normalize_jma_time
    from keirin.scripts.migrate_weather_times import migrate_weather_dir

    assert normalize_jma_time("2026-03-10", "23:59") == "2026-03-10T23:59"
    assert normalize_jma_time("2026-03-31", "24:00") == "2026-04-01T00:00"
    for bad in ("24:10", "12:99", "25:00", "1:5", "ab:cd"):
        assert normalize_jma_time("2026-03-10", bad) == bad

    # a data root without weather/ is left alone, including non-weather Parquet files
    data_dir = tmp_path / "data"
    race_path = data_dir / "race" / "2026" / "2026-03.parquet"
    race_path.parent.mkdir(parents=True)
    race_df = pd.DataFrame([
        {"vel_code": "13", "open_day": "2026-03-10", "race_num": 1, "time": "10:00"},
        {"vel_code": "13", "open_day": "2026-03-10", "race_num": 2, "time": "10:30"},
    ])
    race_df.to_parquet(race_path, index=False)
    before = race_path.read_bytes()
    assert migrate_weather_dir(data_dir) == []
    assert race_path.read_bytes() == before

    # a non-weather file inside weather/ is skipped; a file with an invalid time is refused and unchanged
    w_dir = data_dir / "weather" / "2026"
    w_dir.mkdir(parents=True)
    other = w_dir / "other.parquet"
    race_df.to_parquet(other, index=False)
    other_before = other.read_bytes()
    bad = w_dir / "2026-03.parquet"
    pd.DataFrame([
        {"vel_code": "13", "open_day": "2026-03-10", "time": "00:10", "source": "jma", "temp": 5.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "24:10", "source": "jma", "temp": 4.0},
    ]).to_parquet(bad, index=False)
    bad_before = bad.read_bytes()
    results = migrate_weather_dir(data_dir)
    assert {r.get("skipped") for r in results} == {"not_weather", "invalid_times"}
    assert other.read_bytes() == other_before
    assert bad.read_bytes() == bad_before


def test_weather_time_unification_and_migration(tmp_path: pathlib.Path, monkeypatch):
    # 1) Reader helper normalize_weather_frame: converts "00:10", "9:00", "24:00" (next day), leaves Open-Meteo ISO untouched
    raw_df = pd.DataFrame([
        {"vel_code": "13", "open_day": "2026-03-10", "time": "00:10", "source": "jma", "temp": 5.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "9:00", "source": "jma", "temp": 8.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "24:00", "source": "jma", "temp": 4.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T12:00", "source": "openmeteo", "temp": 11.0},
    ])
    norm_df = normalize_weather_frame(raw_df)
    assert list(norm_df["time"]) == [
        "2026-03-10T00:10",
        "2026-03-10T09:00",
        "2026-03-11T00:00",
        "2026-03-10T12:00",
    ]
    # Original frame is not mutated
    assert list(raw_df["time"]) == ["00:10", "9:00", "24:00", "2026-03-10T12:00"]

    # 2) Storage migration on write: old-format month file merged with new ISO rows deduplicates to one row per observation, keeping newer row
    data_dir = tmp_path / "storage_merge"
    w_path = data_dir / "weather" / "2026" / "2026-03.parquet"
    w_path.parent.mkdir(parents=True, exist_ok=True)

    old_month_df = pd.DataFrame([
        {"vel_code": "13", "open_day": "2026-03-10", "time": "00:10", "source": "jma", "temp": 5.0, "wind_speed": 1.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "24:00", "source": "jma", "temp": 4.0, "wind_speed": 1.2},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T01:00", "source": "openmeteo", "temp": 6.0, "wind_speed": 2.0},
    ])
    old_month_df.to_parquet(w_path, index=False)

    storage = ParquetStorage(data_dir=data_dir)
    new_rows = [
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T00:10", "source": "jma", "temp": 5.5, "wind_speed": 1.5},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T00:20", "source": "jma", "temp": 5.6, "wind_speed": 1.6},
    ]
    storage.save("weather", "2026-03-10", new_rows)

    merged_df = pd.read_parquet(w_path)
    assert len(merged_df) == 4
    assert set(merged_df["time"]) == {
        "2026-03-10T00:10",
        "2026-03-10T00:20",
        "2026-03-11T00:00",
        "2026-03-10T01:00",
    }
    row_0010 = merged_df[(merged_df["time"] == "2026-03-10T00:10") & (merged_df["source"] == "jma")].iloc[0]
    assert row_0010["temp"] == 5.5
    assert row_0010["wind_speed"] == 1.5

    row_om = merged_df[merged_df["source"] == "openmeteo"].iloc[0]
    assert row_om["time"] == "2026-03-10T01:00"
    assert row_om["temp"] == 6.0

    # 3) One-off migration script on a temp directory: dry-run changes nothing; real run converts & deduplicates and is idempotent
    mig_dir = tmp_path / "mig_data"
    mig_file = mig_dir / "weather" / "2026" / "2026-03.parquet"
    mig_file.parent.mkdir(parents=True, exist_ok=True)

    mixed_df = pd.DataFrame([
        {"vel_code": "13", "open_day": "2026-03-10", "time": "00:10", "source": "jma", "temp": 5.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "9:00", "source": "jma", "temp": 8.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "24:00", "source": "jma", "temp": 4.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T00:10", "source": "jma", "temp": 5.9},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T03:00", "source": "openmeteo", "temp": 7.0},
    ])
    mixed_df.to_parquet(mig_file, index=False)
    bytes_before = mig_file.read_bytes()

    # Dry-run via CLI leaves file bytes untouched
    monkeypatch.setattr("sys.argv", ["migrate_weather_times.py", str(mig_dir), "--dry-run"])
    migrate_weather_main()
    assert mig_file.read_bytes() == bytes_before

    # Real run converts 3 HH:MM rows and removes 1 duplicate (keeping newer 5.9 row)
    res1 = migrate_weather_dir(mig_dir, dry_run=False)
    assert len(res1) == 1
    assert res1[0]["rows_before"] == 5
    assert res1[0]["rows_after"] == 4
    assert res1[0]["converted"] == 3
    assert res1[0]["duplicates_removed"] == 1

    after_df = pd.read_parquet(mig_file)
    assert len(after_df) == 4
    assert list(after_df["time"]) == [
        "2026-03-10T09:00",
        "2026-03-11T00:00",
        "2026-03-10T00:10",
        "2026-03-10T03:00",
    ]
    assert after_df[after_df["time"] == "2026-03-10T00:10"].iloc[0]["temp"] == 5.9

    # Second run is idempotent (0 converted, 0 duplicates removed)
    res2 = migrate_weather_dir(mig_dir, dry_run=False)
    assert res2[0]["rows_before"] == 4
    assert res2[0]["rows_after"] == 4
    assert res2[0]["converted"] == 0
    assert res2[0]["duplicates_removed"] == 0
    pd.testing.assert_frame_equal(pd.read_parquet(mig_file), after_df)

def test_2race_empty_final_odds_partial_save_retry_limit_and_next_month(tmp_path: pathlib.Path, monkeypatch):
    """
    Tests High 1 & High 2:
    - A 2-race day where race 2's final odds are empty is NOT marked complete for chariloto,
      while race 1's fetched data IS saved to Parquet and marked done in the per-month progress file.
    - On the next run, only race 2 is re-fetched (race 1 is skipped).
    - After the retry limit (3 attempts across runs), race 2 is marked 'unavailable' with reason,
      the day and month close as complete with known gaps recorded in the progress and marker files,
      previously saved race 1 data is preserved, and select_backfill_month advances to the next month.
    - Explicit cancelled / not-sold races (detected from race_info status or menu) complete immediately
      without requiring final odds or retries.
    """
    venues_config = {
        "13": {
            "vel_code": "13",
            "name": "テスト場",
            "is_indoor": True,
        }
    }

    res_html = (SYNTHETIC_FIXTURES_DIR / "synthetic_results_day_13_2026-03-10.html").read_text(encoding="utf-8")
    r1_results, r1_payouts, r1_lineups = parse_results_day(res_html, vel="13", date="2026-03-10")
    r2_results = [dict(r, race_num=2) for r in r1_results]
    r2_payouts = [dict(p, race_num=2) for p in r1_payouts]
    r2_lineups = [dict(l, race_num=2) for l in r1_lineups]

    race_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_race_info_2026-03-10_13_1.json").read_text(encoding="utf-8"))
    r1_info, r1_entries, r1_pred = parse_race_info(race_json)

    odds_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_odds_final_2016-01-03_13_1.json").read_text(encoding="utf-8"))
    r1_odds = parse_odds(odds_json, snapshot_label="final")

    nk_html = (SYNTHETIC_FIXTURES_DIR / "synthetic_nk_entry_ex.html").read_text(encoding="utf-8")
    nk_parsed = parse_nk_entry(nk_html, "202603101301")

    vel_menu_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_vel_menu_2026-11-17_46.json").read_text(encoding="utf-8"))
    meeting_base, races_base = parse_vel_menu(vel_menu_json)
    meeting_2r = dict(meeting_base, vel_name="テスト場", open_day="2026-03-10")
    races_2r = [r for r in races_base if r["race_num"] in (1, 2)]

    monkeypatch.setattr(
        collect_module,
        "fetch_calendar",
        lambda client, yr, mo: [{"venue_name": "テスト場", "day": 10, "grade": "F2", "flags": []}],
    )
    monkeypatch.setattr(collect_module, "fetch_venue_year_days", lambda client, vel, yr: ["2026-03-10"])
    monkeypatch.setattr(collect_module, "fetch_vel_menu", lambda client, vel, date_str: (meeting_2r, races_2r))
    monkeypatch.setattr(
        collect_module,
        "fetch_results_day",
        lambda client, vel, date_str: (
            [dict(r) for r in (r1_results + r2_results)],
            [dict(p) for p in (r1_payouts + r2_payouts)],
            [dict(l) for l in (r1_lineups + r2_lineups)],
        ),
    )

    fetched_chariloto_races = []
    fetched_odds_races = []
    fetched_nk_races = []

    def mock_fetch_race_info(client, open_day, vel, r_num):
        fetched_chariloto_races.append(r_num)
        return (
            dict(r1_info, open_day=open_day, vel_code=vel, race_num=r_num),
            [dict(e) for e in r1_entries],
            dict(r1_pred),
        )

    def mock_fetch_odds_r2_empty(client, open_day, vel, r_num, snapshot_label=""):
        fetched_odds_races.append(r_num)
        if r_num == 1:
            return [dict(o, snapshot=snapshot_label) for o in r1_odds]
        return []  # Race 2 final odds permanently empty

    def mock_fetch_nk_entry(client, race_id):
        r_num = int(race_id[-2:])
        fetched_nk_races.append(r_num)
        return {
            "race_id": race_id,
            "lineup": nk_parsed["lineup"],
            "entries": [dict(e) for e in nk_parsed["entries"]],
            "honshi": nk_parsed["honshi"],
        }

    monkeypatch.setattr(collect_module, "fetch_race_info", mock_fetch_race_info)
    monkeypatch.setattr(collect_module, "fetch_odds", mock_fetch_odds_r2_empty)
    monkeypatch.setattr(collect_module, "fetch_nk_entry", mock_fetch_nk_entry)
    monkeypatch.setattr(collect_module, "fetch_nk_comments", lambda client, race_id: [])

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    range_file = tmp_path / "config" / "backfill_range.txt"
    range_file.write_text("2026-03-01,2026-04-30\n", encoding="utf-8")
    data_dir = tmp_path / "data"

    # --- Run 1 (Attempt 1/3 for Race 2) ---
    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2026-03-01", "--end", "2026-03-31", "--venues", "13", "--data-dir", str(data_dir)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st = ParquetStorage(data_dir=data_dir)
    # Race 1 and Race 2 were both fetched on Run 1
    assert fetched_chariloto_races == [1, 2]
    assert fetched_odds_races == [1, 2]
    assert fetched_nk_races == [1, 2]

    # Chariloto day and month are NOT complete because R2 final odds were empty
    assert not st.is_done("13", "2026-03-10", "chariloto")
    assert st.is_done("13", "2026-03-10", "netkeirin")
    assert st.is_done("13", "2026-03-10", "weather")
    assert not st.is_done("*", "2026-03-01", "month_complete")

    # Race 1 data was saved and not lost
    df_race = pd.read_parquet(data_dir / "race" / "2026" / "2026-03.parquet")
    df_odds = pd.read_parquet(data_dir / "odds" / "2026" / "2026-03.parquet")
    assert set(df_race["race_num"].tolist()) == {1}
    assert set(df_odds["race_num"].tolist()) == {1}

    # Progress file records R1 done and R2 fail (attempt 1)
    r1_prog = st.get_race_progress("13", "2026-03-10", "chariloto", 1)
    r2_prog = st.get_race_progress("13", "2026-03-10", "chariloto", 2)
    assert r1_prog is not None and r1_prog["status"] == "done"
    assert r2_prog is not None and r2_prog["status"] == "fail" and r2_prog["attempts"] == 1
    assert "final_odds" in r2_prog["reason"]

    # Backfill month selector still selects 2026-03
    sel_run1 = collect_module.select_backfill_month(range_file=range_file, done_dir=data_dir / "_done", jst_today=date(2026, 11, 25))
    assert sel_run1["ym"] == "2026-03"

    # --- Run 2 (Attempt 2/3 for Race 2): only Race 2 is re-fetched ---
    fetched_chariloto_races.clear()
    fetched_odds_races.clear()
    fetched_nk_races.clear()

    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    assert fetched_chariloto_races == [2]
    assert fetched_odds_races == [2]
    assert fetched_nk_races == []  # netkeirin was already done on Run 1
    assert not st.is_done("13", "2026-03-10", "chariloto")
    assert not st.is_done("*", "2026-03-01", "month_complete")
    r2_prog2 = st.get_race_progress("13", "2026-03-10", "chariloto", 2)
    assert r2_prog2 is not None and r2_prog2["status"] == "fail" and r2_prog2["attempts"] == 2

    # --- Run 3 (Attempt 3/3 for Race 2): hits retry limit, marks unavailable, closes day & month ---
    fetched_chariloto_races.clear()
    fetched_odds_races.clear()

    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    assert fetched_chariloto_races == [2]
    assert fetched_odds_races == [2]
    r2_prog3 = st.get_race_progress("13", "2026-03-10", "chariloto", 2)
    assert r2_prog3 is not None and r2_prog3["status"] == "unavailable" and r2_prog3["attempts"] == 3
    assert "final_odds" in r2_prog3["reason"]

    # Day and month are now marked complete with known gaps
    assert st.is_done("13", "2026-03-10", "chariloto")
    assert st.is_done("*", "2026-03-01", "month_complete")

    done_text = (data_dir / "_done" / "2026" / "2026-03.txt").read_text(encoding="utf-8")
    assert "13|2026-03-10|chariloto|known_gaps=R2:missing final_odds" in done_text
    assert "*|2026-03-01|month_complete|known_gaps=1" in done_text

    # Previously saved Race 1 data is still intact in Parquet
    df_race_after = pd.read_parquet(data_dir / "race" / "2026" / "2026-03.parquet")
    df_entry_after = pd.read_parquet(data_dir / "entry" / "2026" / "2026-03.parquet")
    df_odds_after = pd.read_parquet(data_dir / "odds" / "2026" / "2026-03.parquet")
    assert set(df_race_after["race_num"].tolist()) == {1}
    assert len(df_entry_after) == len(r1_entries)
    assert len(df_odds_after) == len(r1_odds)

    # Next month (2026-04) is now selected by select_backfill_month
    sel_after = collect_module.select_backfill_month(range_file=range_file, done_dir=data_dir / "_done", jst_today=date(2026, 11, 25))
    assert sel_after["status"] == "selected"
    assert sel_after["ym"] == "2026-04"

    # --- Explicit not-sold / cancelled race in race_info completes on first run without odds ---
    data_dir_notsold = tmp_path / "data_notsold"

    def mock_fetch_race_info_r2_notsold(client, open_day, vel, r_num):
        info = dict(r1_info, open_day=open_day, vel_code=vel, race_num=r_num)
        if r_num == 2:
            for k in list(info.keys()):
                if k.endswith("_sale_status"):
                    info[k] = "no_sale"
        return info, [dict(e) for e in r1_entries], dict(r1_pred)

    monkeypatch.setattr(collect_module, "fetch_race_info", mock_fetch_race_info_r2_notsold)
    monkeypatch.setattr(
        "sys.argv",
        ["collect.py", "--start", "2026-03-10", "--end", "2026-03-10", "--venues", "13", "--data-dir", str(data_dir_notsold)],
    )
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        collect_module.main()

    st_notsold = ParquetStorage(data_dir=data_dir_notsold)
    assert st_notsold.is_done("13", "2026-03-10", "chariloto")
    r2_ns_prog = st_notsold.get_race_progress("13", "2026-03-10", "chariloto", 2)
    assert r2_ns_prog is not None and r2_ns_prog["status"] == "done" and r2_ns_prog["reason"] == "cancelled_or_not_sold"



def test_rate_limit_mid_day_does_not_mark_unsaved_races_done(tmp_path: pathlib.Path, monkeypatch):
    """A 429 on race 2 must not leave race 1 marked done without its rows saved (no silent data loss)."""
    venues_config = {"13": {"vel_code": "13", "name": "テスト場", "is_indoor": True}}

    res_html = (SYNTHETIC_FIXTURES_DIR / "synthetic_results_day_13_2026-03-10.html").read_text(encoding="utf-8")
    r1_results, r1_payouts, r1_lineups = parse_results_day(res_html, vel="13", date="2026-03-10")
    r2_results = [dict(r, race_num=2) for r in r1_results]
    r2_lineups = [dict(l, race_num=2) for l in r1_lineups]
    race_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_race_info_2026-03-10_13_1.json").read_text(encoding="utf-8"))
    r1_info, r1_entries, r1_pred = parse_race_info(race_json)
    odds_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_odds_final_2016-01-03_13_1.json").read_text(encoding="utf-8"))
    r1_odds = parse_odds(odds_json, snapshot_label="final")
    vel_menu_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_vel_menu_2026-11-17_46.json").read_text(encoding="utf-8"))
    meeting_base, races_base = parse_vel_menu(vel_menu_json)
    meeting_2r = dict(meeting_base, vel_name="テスト場", open_day="2026-03-10")
    races_2r = [r for r in races_base if r["race_num"] in (1, 2)]

    monkeypatch.setattr(collect_module, "fetch_calendar",
                        lambda client, yr, mo: [{"venue_name": "テスト場", "day": 10, "grade": "F2", "flags": []}])
    monkeypatch.setattr(collect_module, "fetch_venue_year_days", lambda client, vel, yr: ["2026-03-10"])
    monkeypatch.setattr(collect_module, "fetch_vel_menu", lambda client, vel, date_str: (meeting_2r, races_2r))
    monkeypatch.setattr(collect_module, "fetch_results_day", lambda client, vel, date_str: (
        [dict(r) for r in (r1_results + r2_results)],
        [dict(p) for p in r1_payouts],
        [dict(l) for l in (r1_lineups + r2_lineups)],
    ))
    monkeypatch.setattr(collect_module, "fetch_race_info", lambda client, open_day, vel, r_num: (
        dict(r1_info, open_day=open_day, vel_code=vel, race_num=r_num),
        [dict(e) for e in r1_entries],
        dict(r1_pred),
    ))

    def odds_429_on_r2(client, open_day, vel, r_num, snapshot_label=""):
        if r_num == 2:
            raise ChariRateLimitError("429 Retry-After too long")
        return [dict(o, snapshot=snapshot_label) for o in r1_odds]

    monkeypatch.setattr(collect_module, "fetch_odds", odds_429_on_r2)
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    monkeypatch.setattr("sys.argv", ["collect.py", "--start", "2026-03-01", "--end", "2026-03-31", "--venues", "13",
                                     "--data-dir", str(data_dir), "--sources", "chariloto"])
    with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
        try:
            collect_module.main()
        except SystemExit:
            pass

    st = ParquetStorage(data_dir=data_dir)
    assert not st.is_done("13", "2026-03-10", "chariloto")
    race_path = data_dir / "race" / "2026" / "2026-03.parquet"
    saved = set(pd.read_parquet(race_path)["race_num"].tolist()) if race_path.exists() else set()
    for rn in (1, 2):
        if st.is_race_done("13", "2026-03-10", "chariloto", rn):
            assert rn in saved, f"R{rn} is marked done but its rows were never saved"


@pytest.mark.parametrize("case", ["empty_results", "empty_menu"])
def test_whole_day_gap_respects_retry_limit_and_closes_day(tmp_path: pathlib.Path, monkeypatch, case):
    """Results empty for the whole day (or an empty menu) are counted per race / per day and close after the limit."""
    venues_config = {"13": {"vel_code": "13", "name": "テスト場", "is_indoor": True}}
    race_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_race_info_2026-03-10_13_1.json").read_text(encoding="utf-8"))
    r1_info, r1_entries, r1_pred = parse_race_info(race_json)
    odds_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_odds_final_2016-01-03_13_1.json").read_text(encoding="utf-8"))
    r1_odds = parse_odds(odds_json, snapshot_label="final")
    vel_menu_json = json.loads((SYNTHETIC_FIXTURES_DIR / "synthetic_vel_menu_2026-11-17_46.json").read_text(encoding="utf-8"))
    meeting_base, races_base = parse_vel_menu(vel_menu_json)
    meeting_2r = dict(meeting_base, vel_name="テスト場", open_day="2026-03-10")
    races_2r = [r for r in races_base if r["race_num"] in (1, 2)] if case == "empty_results" else [{"race_num": None}]

    monkeypatch.setattr(collect_module, "fetch_calendar",
                        lambda client, yr, mo: [{"venue_name": "テスト場", "day": 10, "grade": "F2", "flags": []}])
    monkeypatch.setattr(collect_module, "fetch_venue_year_days", lambda client, vel, yr: ["2026-03-10"])
    monkeypatch.setattr(collect_module, "_is_confirmed_no_races", lambda meeting, races: False)
    monkeypatch.setattr(collect_module, "fetch_vel_menu", lambda client, vel, date_str: (meeting_2r, races_2r))
    results_calls = []

    def empty_results(client, vel, date_str):
        results_calls.append(date_str)
        return [], [], []

    monkeypatch.setattr(collect_module, "fetch_results_day", empty_results)
    monkeypatch.setattr(collect_module, "fetch_race_info", lambda client, open_day, vel, r_num: (
        dict(r1_info, open_day=open_day, vel_code=vel, race_num=r_num), [dict(e) for e in r1_entries], dict(r1_pred)))
    monkeypatch.setattr(collect_module, "fetch_odds",
                        lambda client, open_day, vel, r_num, snapshot_label="": [dict(o) for o in r1_odds])
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    argv = ["collect.py", "--start", "2026-03-01", "--end", "2026-03-31", "--venues", "13",
            "--data-dir", str(data_dir), "--sources", "chariloto", "--max-retries", "3"]

    for run in range(1, 5):
        monkeypatch.setattr("sys.argv", list(argv))
        with patch("keirin.scripts.collect.load_venues_config", return_value=venues_config):
            try:
                collect_module.main()
            except SystemExit:
                pass
        st = ParquetStorage(data_dir=data_dir)
        if run < 3:
            assert not st.is_done("13", "2026-03-10", "chariloto"), f"closed too early on run {run}"
        else:
            assert st.is_done("13", "2026-03-10", "chariloto"), f"not closed on run {run}"

    # the day is not fetched again once it is closed with known gaps
    assert len(results_calls) == 3
    done_text = (data_dir / "_done" / "2026" / "2026-03.txt").read_text(encoding="utf-8")
    assert "13|2026-03-10|chariloto|known_gaps=" in done_text
    gaps = ParquetStorage(data_dir=data_dir).get_month_gaps("2026-03")
    assert gaps and all(g["status"] == "unavailable" for g in gaps)


def test_invalid_weather_times_are_never_written(tmp_path: pathlib.Path):
    from keirin.scripts.migrate_weather_times import migrate_weather_dir
    from keirin.storage import is_valid_weather_time

    assert is_valid_weather_time("2026-03-10T23:59")
    for bad in ("9:0", "2026-03-10T12:99", "2026-03-10T24:00", "2026-02-30T10:00", "24:10", "", None):
        assert not is_valid_weather_time(bad)

    # migration: one bad value mixed with good old-format rows -> file left unchanged
    w_dir = tmp_path / "data" / "weather" / "2026"
    w_dir.mkdir(parents=True)
    f = w_dir / "2026-03.parquet"
    pd.DataFrame([
        {"vel_code": "13", "open_day": "2026-03-10", "time": "00:10", "source": "jma", "temp": 5.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "9:0", "source": "jma", "temp": 6.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T12:99", "source": "openmeteo", "temp": 7.0},
    ]).to_parquet(f, index=False)
    before = f.read_bytes()
    res = migrate_weather_dir(tmp_path / "data")
    assert res[0]["skipped"] == "invalid_times" and res[0]["invalid_times"] == 2
    assert f.read_bytes() == before

    # normal save: an existing file with an invalid time is not rewritten
    st = ParquetStorage(data_dir=tmp_path / "data")
    new_rows = [{"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T01:00", "source": "openmeteo", "temp": 3.0}]
    with pytest.raises(StorageError):
        st.save("weather", "2026-03-10", new_rows)
    assert f.read_bytes() == before

    # normal save: invalid new rows are dropped, valid ones are kept
    st2 = ParquetStorage(data_dir=tmp_path / "data2")
    st2.save("weather", "2026-03-10", [
        {"vel_code": "13", "open_day": "2026-03-10", "time": "24:10", "source": "jma", "temp": 1.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "2026-03-10T12:99", "source": "openmeteo", "temp": 2.0},
        {"vel_code": "13", "open_day": "2026-03-10", "time": "00:20", "source": "jma", "temp": 3.0},
    ])
    saved = pd.read_parquet(tmp_path / "data2" / "weather" / "2026" / "2026-03.parquet")
    assert list(saved["time"]) == ["2026-03-10T00:20"]
