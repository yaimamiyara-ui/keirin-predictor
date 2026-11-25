import json
import logging
import os
import pathlib
import re
from typing import Any, Dict, List, Optional, Set, Union
import pandas as pd

from keirin.fetch.jma import normalize_jma_time

logger = logging.getLogger(__name__)

_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")


def normalize_weather_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of weather DataFrame with 'HH:MM'/'H:MM' times normalized to JST ISO datetimes ('YYYY-MM-DDTHH:MM')."""
    out = df.copy()
    if out.empty or "time" not in out.columns or "open_day" not in out.columns:
        return out

    def _conv(row: pd.Series) -> Any:
        t = row["time"]
        d = row["open_day"]
        if pd.isna(t) or pd.isna(d):
            return t
        t_str = str(t).strip()
        if _HHMM_RE.match(t_str):
            return normalize_jma_time(str(d).strip(), t_str)
        return t

    out["time"] = out.apply(_conv, axis=1)
    return out


class StorageError(RuntimeError):
    """Raised when an existing Parquet file cannot be read or written safely."""
    pass

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
        self.unreadable_months: Set[str] = set()

    def _get_parquet_path(self, kind: str, open_day: str) -> pathlib.Path:
        year = open_day[:4]
        ym = open_day[:7]
        return self.data_dir / kind / year / f"{ym}.parquet"

    def _get_done_path(self, open_day: str) -> pathlib.Path:
        year = open_day[:4]
        ym = open_day[:7]
        return self.data_dir / "_done" / year / f"{ym}.txt"

    def _get_progress_path(self, open_day: str) -> pathlib.Path:
        year = open_day[:4]
        ym = open_day[:7]
        return self.data_dir / "_done" / year / f"{ym}_progress.txt"

    def _load_progress_map(self, open_day: str) -> Dict[str, Dict[str, Any]]:
        prog_path = self._get_progress_path(open_day)
        result: Dict[str, Dict[str, Any]] = {}
        if not prog_path.exists():
            return result
        with open(prog_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                parts = stripped.split("|", 5)
                if len(parts) < 5:
                    continue
                vel_code, day_str, source, r_str, status = parts[:5]
                rest = parts[5] if len(parts) > 5 else ""
                attempts = 0
                reason = ""
                if status == "fail":
                    sub = rest.split("|", 1)
                    try:
                        attempts = int(sub[0])
                    except ValueError:
                        attempts = 1
                    reason = sub[1] if len(sub) > 1 else ""
                elif status == "unavailable":
                    sub = rest.split("|", 1)
                    try:
                        attempts = int(sub[0])
                        reason = sub[1] if len(sub) > 1 else ""
                    except ValueError:
                        attempts = 0
                        reason = rest
                else:
                    reason = rest
                key = f"{vel_code}|{day_str}|{source}|{r_str}"
                result[key] = {
                    "vel_code": vel_code,
                    "open_day": day_str,
                    "source": source,
                    "race_num": int(r_str) if r_str.isdigit() else r_str,
                    "status": status,
                    "attempts": attempts,
                    "reason": reason,
                }
        return result

    def _save_progress_map(self, open_day: str, prog_map: Dict[str, Dict[str, Any]]) -> None:
        ym = open_day[:7]
        if ym in self.unreadable_months:
            logger.error(f"Refusing to write progress for {ym}: month has unreadable Parquet.")
            return
        prog_path = self._get_progress_path(open_day)
        prog_path.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        for key in sorted(prog_map.keys()):
            info = prog_map[key]
            st = info["status"]
            vel = info["vel_code"]
            d = info["open_day"]
            src = info["source"]
            rn = info["race_num"]
            reason = str(info.get("reason") or "").replace("\n", " ").replace("\r", " ")
            attempts = int(info.get("attempts") or 0)
            if st == "done":
                line = f"{vel}|{d}|{src}|{rn}|done"
                if reason:
                    line += f"|{reason}"
            elif st in ("fail", "unavailable"):
                line = f"{vel}|{d}|{src}|{rn}|{st}|{attempts}|{reason}"
            else:
                line = f"{vel}|{d}|{src}|{rn}|{st}"
                if reason:
                    line += f"|{reason}"
            lines.append(line)
        tmp_path = prog_path.with_suffix(".txt.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            for l in lines:
                f.write(f"{l}\n")
        tmp_path.replace(prog_path)

    def get_race_progress(self, vel_code: str, open_day: str, source: str, race_num: Union[int, str]) -> Optional[Dict[str, Any]]:
        prog_map = self._load_progress_map(open_day)
        key = f"{vel_code}|{open_day}|{source}|{race_num}"
        return prog_map.get(key)

    def is_race_done(self, vel_code: str, open_day: str, source: str, race_num: Union[int, str]) -> bool:
        if self.is_done(vel_code, open_day, source):
            return True
        info = self.get_race_progress(vel_code, open_day, source, race_num)
        return bool(info and info.get("status") in ("done", "unavailable"))

    def mark_race_done(self, vel_code: str, open_day: str, source: str, race_num: Union[int, str], reason: str = "") -> None:
        ym = open_day[:7]
        if ym in self.unreadable_months:
            return
        prog_map = self._load_progress_map(open_day)
        key = f"{vel_code}|{open_day}|{source}|{race_num}"
        existing = prog_map.get(key)
        if existing and existing.get("status") == "done" and existing.get("reason", "") == reason:
            return
        if not existing or existing.get("status") != "done":
            type(self).new_done_count += 1
        prog_map[key] = {
            "vel_code": vel_code,
            "open_day": open_day,
            "source": source,
            "race_num": race_num,
            "status": "done",
            "attempts": existing.get("attempts", 0) if existing else 0,
            "reason": reason,
        }
        self._save_progress_map(open_day, prog_map)

    def record_race_failure(
        self,
        vel_code: str,
        open_day: str,
        source: str,
        race_num: Union[int, str],
        reason: str,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        ym = open_day[:7]
        if ym in self.unreadable_months:
            return {"status": "fail", "attempts": 0, "reason": reason}
        prog_map = self._load_progress_map(open_day)
        key = f"{vel_code}|{open_day}|{source}|{race_num}"
        existing = prog_map.get(key)
        if existing and existing.get("status") in ("done", "unavailable"):
            return existing
        attempts = (int(existing.get("attempts", 0)) if existing else 0) + 1
        status = "unavailable" if attempts >= max_retries else "fail"
        type(self).new_done_count += 1
        entry = {
            "vel_code": vel_code,
            "open_day": open_day,
            "source": source,
            "race_num": race_num,
            "status": status,
            "attempts": attempts,
            "reason": reason,
        }
        prog_map[key] = entry
        self._save_progress_map(open_day, prog_map)
        return entry

    def get_month_gaps(self, ym: str) -> List[Dict[str, Any]]:
        prog_map = self._load_progress_map(f"{ym}-01")
        return [
            info
            for key, info in sorted(prog_map.items())
            if info.get("status") == "unavailable"
        ]

    def save(self, kind: str, open_day: str, records: Union[List[Dict[str, Any]], pd.DataFrame]) -> None:
        if isinstance(records, pd.DataFrame):
            df_new = records.copy()
        else:
            if not records:
                return
            df_new = pd.DataFrame(records)

        if df_new.empty:
            return

        ym = open_day[:7]
        if ym in self.unreadable_months:
            logger.error(f"Refusing to write {kind} for {ym}: month has an unreadable Parquet file.")
            raise StorageError(f"Month {ym} is locked due to an unreadable Parquet file")

        target_path = self._get_parquet_path(kind, open_day)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists():
            try:
                df_existing = pd.read_parquet(target_path)
            except Exception as e:
                self.unreadable_months.add(ym)
                logger.error(f"Failed to read existing Parquet {target_path}; keeping file and stopping write: {e}")
                raise StorageError(f"Cannot read existing Parquet {target_path}: {e}") from e
            if kind == "weather":
                df_existing = normalize_weather_frame(df_existing)
                df_new = normalize_weather_frame(df_new)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        else:
            if kind == "weather":
                df_new = normalize_weather_frame(df_new)
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
        target_prefix = f"{target_line}|"
        with open(done_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped == target_line or stripped.startswith(target_prefix):
                    return True
        return False

    # この実行で新しく取得済みにした数（空回り防止に使う。Claude追加）
    new_done_count = 0

    def add_done(self, vel_code: str, open_day: str, kind: str, detail: str = "") -> None:
        ym = open_day[:7]
        if ym in self.unreadable_months:
            logger.error(f"Refusing to mark done for {vel_code} {open_day} {kind}: month {ym} has unreadable Parquet.")
            return

        done_path = self._get_done_path(open_day)
        done_path.parent.mkdir(parents=True, exist_ok=True)

        clean_detail = detail.replace("\n", " ").replace("\r", " ").strip()
        target_line = f"{vel_code}|{open_day}|{kind}" if not clean_detail else f"{vel_code}|{open_day}|{kind}|{clean_detail}"
        base_line = f"{vel_code}|{open_day}|{kind}"
        base_prefix = f"{base_line}|"

        lines = set()
        if done_path.exists():
            with open(done_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        lines.add(stripped)

        if target_line in lines:
            return
        if not clean_detail and any(l == base_line or l.startswith(base_prefix) for l in lines):
            return

        lines = {l for l in lines if not (l == base_line or l.startswith(base_prefix))}

        # 月完了の印も「進んだ」に数える（次の月へ続けて進むため）
        type(self).new_done_count += 1

        lines.add(target_line)
        sorted_lines = sorted(list(lines))

        tmp_path = done_path.with_suffix(".txt.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            for l in sorted_lines:
                f.write(f"{l}\n")
        tmp_path.replace(done_path)
