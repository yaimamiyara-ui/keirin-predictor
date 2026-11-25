import argparse
import pathlib
from typing import Any, Dict, List, Union
import pandas as pd

from keirin.storage import KEYS_MAP, _HHMM_RE, normalize_weather_frame


def _is_hhmm_time(val: Any) -> bool:
    if pd.isna(val):
        return False
    return bool(_HHMM_RE.match(str(val).strip()))


def migrate_weather_file(path: pathlib.Path, dry_run: bool = False) -> Dict[str, Any]:
    df = pd.read_parquet(path)
    rows_before = len(df)

    if "time" in df.columns and "open_day" in df.columns and not df.empty:
        converted = int(
            df.apply(lambda r: _is_hhmm_time(r["time"]) and not pd.isna(r["open_day"]), axis=1).sum()
        )
    else:
        converted = 0

    df_norm = normalize_weather_frame(df)

    keys = [k for k in KEYS_MAP["weather"] if k in df_norm.columns]
    if keys:
        df_dedup = df_norm.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)
    else:
        df_dedup = df_norm

    rows_after = len(df_dedup)
    duplicates_removed = rows_before - rows_after

    print(
        f"{path}: before={rows_before} after={rows_after} "
        f"converted={converted} duplicates_removed={duplicates_removed}"
        + (" [dry-run]" if dry_run else "")
    )

    if not dry_run and (converted > 0 or duplicates_removed > 0):
        tmp_path = path.with_suffix(".parquet.tmp")
        df_dedup.to_parquet(tmp_path, index=False)
        tmp_path.replace(path)

    return {
        "path": str(path),
        "rows_before": rows_before,
        "rows_after": rows_after,
        "converted": converted,
        "duplicates_removed": duplicates_removed,
    }


def migrate_weather_dir(data_dir: Union[str, pathlib.Path], dry_run: bool = False) -> List[Dict[str, Any]]:
    base = pathlib.Path(data_dir)
    weather_root = base / "weather" if (base / "weather").is_dir() else base
    files = sorted(weather_root.rglob("*.parquet"))
    results = []
    for p in files:
        if p.name.endswith(".parquet.tmp"):
            continue
        results.append(migrate_weather_file(p, dry_run=dry_run))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert weather Parquet files from JMA HH:MM times to JST ISO datetimes (YYYY-MM-DDTHH:MM)."
    )
    parser.add_argument("data_dir", type=str, nargs="?", default=None, help="Local data directory path")
    parser.add_argument("--data-dir", dest="data_dir_opt", type=str, default=None, help="Local data directory path")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and print counts without modifying files")
    args = parser.parse_args()

    target_dir = args.data_dir_opt or args.data_dir
    if not target_dir:
        parser.error("data_dir argument is required")

    migrate_weather_dir(target_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
