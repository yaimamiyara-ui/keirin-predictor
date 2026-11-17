import os
import pathlib
from typing import Any, Dict, List, Union
import pandas as pd

KEYS_MAP: Dict[str, List[str]] = {
    "race": ["vel_code", "open_day", "race_num"],
    "entry": ["vel_code", "open_day", "race_num", "entry_num"],
    "prediction": ["vel_code", "open_day", "race_num"],
    "odds": ["vel_code", "open_day", "race_num", "bet_type", "combo", "snapshot"],
    "result": ["vel_code", "open_day", "race_num", "car_num"],
    "payout": ["vel_code", "open_day", "race_num", "bet_type", "combo"],
    "lineup": ["vel_code", "open_day", "race_num", "source"],
    "nk_entry": ["vel_code", "open_day", "race_num", "car_num"],
    "nk_comment": ["vel_code", "open_day", "race_num", "car_num"],
    "weather": ["vel_code", "open_day", "time", "source"],
}


class ParquetStorage:

    def __init__(self, data_dir: Union[str, pathlib.Path] = "data"):
        self.data_dir = pathlib.Path(data_dir)

    def _get_parquet_path(self, kind: str, open_day: str) -> pathlib.Path:
        year = open_day[:4]
        ym = open_day[:7]
        return self.data_dir / kind / year / f"{ym}.parquet"

    def _get_done_path(self, open_day: str) -> pathlib.Path:
        year = open_day[:4]
        ym = open_day[:7]
        return self.data_dir / "_done" / year / f"{ym}.txt"

    def save(self, kind: str, open_day: str, records: Union[List[Dict[str, Any]], pd.DataFrame]) -> None:
        if isinstance(records, pd.DataFrame):
            df_new = records.copy()
        else:
            if not records:
                return
            df_new = pd.DataFrame(records)

        if df_new.empty:
            return

        target_path = self._get_parquet_path(kind, open_day)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists():
            try:
                df_existing = pd.read_parquet(target_path)
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            except Exception:
                df_combined = df_new
        else:
            df_combined = df_new

        keys = KEYS_MAP.get(kind)
        if keys:
            valid_keys = [k for k in keys if k in df_combined.columns]
            if valid_keys:
                df_combined = df_combined.drop_duplicates(subset=valid_keys, keep="last")

        tmp_path = target_path.with_suffix(".parquet.tmp")
        df_combined.to_parquet(tmp_path, index=False)
        tmp_path.replace(target_path)

    def is_done(self, vel_code: str, open_day: str, kind: str) -> bool:
        done_path = self._get_done_path(open_day)
        if not done_path.exists():
            return False

        target_line = f"{vel_code}|{open_day}|{kind}"
        with open(done_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == target_line:
                    return True
        return False

    def add_done(self, vel_code: str, open_day: str, kind: str) -> None:
        done_path = self._get_done_path(open_day)
        done_path.parent.mkdir(parents=True, exist_ok=True)

        target_line = f"{vel_code}|{open_day}|{kind}"
        lines = set()
        if done_path.exists():
            with open(done_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        lines.add(stripped)

        if target_line in lines:
            return

        lines.add(target_line)
        sorted_lines = sorted(list(lines))

        tmp_path = done_path.with_suffix(".txt.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            for l in sorted_lines:
                f.write(f"{l}\n")
        tmp_path.replace(done_path)
