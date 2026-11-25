import json
import pathlib
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from keirin.storage import ParquetStorage, StorageError
from keirin.fetch.http import PoliteClient, PoliteError, PoliteRateLimitError
from keirin.fetch.client import ChariClient, ChariError, ChariRateLimitError
import keirin.scripts.collect as collect_module


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
        payouts = [{"open_day": date, "vel_code": vel, "race_num": 1, "bet_type": "nirentan", "combo": "1-2", "payout_yen": 500}]
        lineups = [{"open_day": date, "vel_code": vel, "race_num": 1, "lineup": "1-2-3", "line_count": 1}]
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
