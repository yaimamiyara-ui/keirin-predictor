import json
import pathlib
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from keirin.storage import ParquetStorage
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
