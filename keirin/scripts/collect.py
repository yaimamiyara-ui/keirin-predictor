import argparse
import json
import logging
import pathlib
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from keirin.fetch.chariloto import (
    fetch_calendar,
    fetch_lineup,
    fetch_odds,
    fetch_race_info,
    fetch_results_day,
    fetch_vel_menu,
    fetch_venue_year_days,
)
from keirin.fetch.client import ChariClient, ChariError, ChariRateLimitError
from keirin.fetch.http import PoliteClient, PoliteError, PoliteRateLimitError
from keirin.fetch.jma import fetch_jma_10min, normalize_jma_time
from keirin.fetch.netkeirin import fetch_nk_comments, fetch_nk_entry, make_race_id
from keirin.fetch.openmeteo import fetch_openmeteo_hourly
from keirin.storage import ParquetStorage, StorageError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_venues_config(config_path: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    if not config_path.exists():
        logger.warning(f"Venues config not found at {config_path}")
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    venues = {}
    if isinstance(data, list):
        for item in data:
            v_code = str(item.get("vel_code", "")).zfill(2)
            venues[v_code] = item
    elif isinstance(data, dict):
        for k, v in data.items():
            v_code = str(v.get("vel_code", k)).zfill(2)
            v["vel_code"] = v_code
            venues[v_code] = v
    return venues


def get_year_month_range(start_date: str, end_date: str) -> List[Tuple[int, int]]:
    s_dt = datetime.strptime(start_date, "%Y-%m-%d")
    e_dt = datetime.strptime(end_date, "%Y-%m-%d")
    cur = datetime(s_dt.year, s_dt.month, 1)
    end = datetime(e_dt.year, e_dt.month, 1)

    ym_list = []
    while cur <= end:
        ym_list.append((cur.year, cur.month))
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1)
        else:
            cur = datetime(cur.year, cur.month + 1, 1)
    return ym_list


def _is_confirmed_no_races(meeting: Dict[str, Any], races_menu: List[Dict[str, Any]]) -> bool:
    if races_menu:
        return False
    if not isinstance(meeting, dict) or not meeting:
        return False
    cancel_keywords = ("中止", "順延", "開催なし", "不成立")
    for field in ("rehold_string", "spent_days_string", "sub_name", "race_name"):
        val = meeting.get(field)
        if val and any(kw in str(val) for kw in cancel_keywords):
            return True
    return False


def _is_race_cancelled(race_item: Dict[str, Any]) -> bool:
    if not isinstance(race_item, dict):
        return False
    cancel_keywords = ("中止", "順延", "開催なし", "不成立")
    for field in ("event_name", "status", "rehold_string", "cancel_reason", "note"):
        val = race_item.get(field)
        if val and any(kw in str(val) for kw in cancel_keywords):
            return True
    return False


def select_backfill_month(
    range_file: pathlib.Path = pathlib.Path("config/backfill_range.txt"),
    done_dir: pathlib.Path = pathlib.Path("data/_done"),
    jst_today: Optional[date] = None,
) -> Dict[str, str]:
    s_str, e_str = range_file.read_text(encoding="utf-8").strip().split(",")
    s, e = date.fromisoformat(s_str.strip()), date.fromisoformat(e_str.strip())
    if jst_today is None:
        jst_today = (datetime.utcnow() + timedelta(hours=9)).date()
    daily_from = (jst_today - timedelta(days=1)).replace(day=1)

    cur = s.replace(day=1)
    while cur <= e and cur < daily_from:
        f = done_dir / f"{cur:%Y}" / f"{cur:%Y-%m}.txt"
        if not (f.exists() and f"*|{cur:%Y-%m}-01|month_complete" in f.read_text(encoding="utf-8")):
            nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            return {
                "status": "selected",
                "start": str(max(s, cur)),
                "end": str(min(e, nxt - timedelta(days=1))),
                "ym": f"{cur:%Y-%m}",
                "y": f"{cur:%Y}",
                "done_inc": f"_done/{cur:%Y}/{cur:%Y-%m}*.txt",
                "parquet_inc": f"*/{cur:%Y}/{cur:%Y-%m}.parquet",
            }
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    while cur <= e:
        f = done_dir / f"{cur:%Y}" / f"{cur:%Y-%m}.txt"
        if not (f.exists() and f"*|{cur:%Y-%m}-01|month_complete" in f.read_text(encoding="utf-8")):
            return {
                "status": "deferred",
                "done": "1",
                "deferred": "1",
                "ym": f"{cur:%Y-%m}",
            }
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    return {
        "status": "complete",
        "done": "1",
        "deferred": "0",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Keirin data collector script")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--venues", type=str, default="", help="Comma separated venue codes or names")
    parser.add_argument("--sources", type=str, default="chariloto,netkeirin,weather", help="Comma separated sources")
    parser.add_argument("--max-minutes", type=int, default=300, help="Max execution time in minutes")
    parser.add_argument("--data-dir", type=str, default="data", help="Data directory path")
    parser.add_argument("--snapshot", type=str, default="final", help="Odds snapshot label")
    parser.add_argument("--today-mode", action="store_true", help="Mode for fetching today's upcoming races")

    args = parser.parse_args()

    ParquetStorage.new_done_count = 0
    start_mono = time.monotonic()
    max_seconds = args.max_minutes * 60
    deadline = start_mono + max_seconds

    storage = ParquetStorage(data_dir=args.data_dir)
    chari_client = ChariClient(deadline=deadline)
    polite_client = PoliteClient(deadline=deadline)

    venues_config = load_venues_config(pathlib.Path("config/venues.json"))

    name_to_code = {}
    for code, info in venues_config.items():
        name_to_code[info.get("name", "")] = code
        name_to_code[code] = code

    target_venues: Set[str] = set()
    if args.venues:
        for v in args.venues.split(","):
            v_clean = v.strip()
            if v_clean in name_to_code:
                target_venues.add(name_to_code[v_clean])
            elif v_clean.zfill(2) in venues_config:
                target_venues.add(v_clean.zfill(2))
    else:
        target_venues = set(venues_config.keys())

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    consecutive_errors = 0
    any_failure = False  # 1つでも取得に失敗したら、その月を「完了」にしない（Claude追加）
    timed_out = False

    def check_error_threshold():
        nonlocal consecutive_errors, any_failure
        any_failure = True
        consecutive_errors += 1
        if consecutive_errors >= 10:
            logger.error("Consecutive 10 HTTP/Chari errors occurred. Exiting script.")
            sys.exit(1)

    ym_range = get_year_month_range(args.start, args.end)
    calendar_active_venues: Set[str] = set()

    for yr, mo in ym_range:
        try:
            cal_rows = fetch_calendar(chari_client, yr, mo)
            consecutive_errors = 0
            for row in cal_rows:
                v_name = row.get("venue_name")
                if v_name in name_to_code:
                    v_code = name_to_code[v_name]
                    if not target_venues or v_code in target_venues:
                        calendar_active_venues.add(v_code)
        except Exception as e:
            # 過去の月はカレンダーのページが無い（404）ことがある。カレンダーは場の絞り込みにしか使わないので、
            # 失敗扱いにせず全場を対象にする（開催日は場ごとの開催日一覧で確定する）。Claude修正 2026-11-21
            logger.warning(f"Calendar not available for {yr}-{mo:02d} ({e}); using all venues")
            calendar_active_venues |= set(target_venues or [])

    if not calendar_active_venues and target_venues:
        calendar_active_venues = target_venues

    venue_year_days_cache: Dict[Tuple[str, int], List[str]] = {}
    years = sorted(list(set(yr for yr, _ in ym_range)))

    scheduled_days: Set[Tuple[str, str]] = set()

    if args.today_mode:
        # 当日分：結果ページの開催日一覧には当日以降がまだ載らないので、開催カレンダーの日付を使う（Claude修正）
        for yr, mo in ym_range:
            try:
                for row in fetch_calendar(chari_client, yr, mo):
                    v_code = name_to_code.get(row.get("venue_name"))
                    if not v_code or (target_venues and v_code not in target_venues):
                        continue
                    d = f"{yr:04d}-{mo:02d}-{int(row.get('day')):02d}"
                    if args.start <= d <= args.end:
                        scheduled_days.add((v_code, d))
            except Exception as e:
                logger.warning(f"Failed to fetch calendar for today-mode {yr}-{mo:02d}: {e}")
                check_error_threshold()
    for v_code in ([] if args.today_mode else sorted(list(calendar_active_venues))):
        for yr in years:
            try:
                days = fetch_venue_year_days(chari_client, v_code, yr)
                consecutive_errors = 0
                venue_year_days_cache[(v_code, yr)] = days
                for d in days:
                    if args.start <= d <= args.end:
                        scheduled_days.add((v_code, d))
            except Exception as e:
                logger.warning(f"Failed to fetch venue year days for {v_code} year {yr}: {e}")
                check_error_threshold()

    sorted_schedule = sorted(list(scheduled_days), key=lambda x: (x[1], x[0]))
    logger.info(f"Total scheduled venue-days to process: {len(sorted_schedule)}")

    skipped_sources: Set[str] = set()

    for vel_code, open_day in sorted_schedule:
        if time.monotonic() >= deadline:
            logger.info("Reached max execution time limit. Stopping execution safely.")
            timed_out = True
            break

        if open_day[:7] in storage.unreadable_months:
            logger.error(f"Skipping {vel_code} {open_day} because month {open_day[:7]} has an unreadable Parquet file.")
            any_failure = True
            continue

        vd_start = time.monotonic()
        venue_info = venues_config.get(vel_code, {})

        if args.today_mode:
            if "chariloto" in skipped_sources:
                any_failure = True
                continue
            try:
                meeting, races_menu = fetch_vel_menu(chari_client, vel_code, open_day)
                consecutive_errors = 0

                if _is_confirmed_no_races(meeting, races_menu):
                    logger.info(f"[Today-Mode] Confirmed no races for {vel_code} {open_day}")
                    continue

                r_nums_menu = [r.get("race_num") for r in races_menu if r.get("race_num")]
                if not r_nums_menu:
                    raise ValueError(f"Empty vel_menu races for scheduled today-mode day {vel_code} {open_day}")

                r_rows, e_rows, p_rows, o_rows, l_rows = [], [], [], [], []
                nk_e_rows, nk_c_rows = [], []

                for r_item in races_menu:
                    if time.monotonic() >= deadline:
                        logger.info("Reached max execution time limit inside today-mode race loop. Stopping safely.")
                        timed_out = True
                        break

                    r_num = r_item.get("race_num")
                    if not r_num:
                        continue

                    r_info, entries, pred = fetch_race_info(chari_client, open_day, vel_code, r_num)
                    r_info.update(meeting)
                    r_rows.append(r_info)

                    for e in entries:
                        e["open_day"] = open_day
                        e["vel_code"] = vel_code
                        e["race_num"] = r_num
                        e_rows.append(e)

                    pred["open_day"] = open_day
                    pred["vel_code"] = vel_code
                    pred["race_num"] = r_num
                    p_rows.append(pred)

                    lineup_info = fetch_lineup(chari_client, open_day, vel_code, r_num)
                    if lineup_info.get("lineup"):
                        l_rows.append({
                            "open_day": open_day,
                            "vel_code": vel_code,
                            "race_num": r_num,
                            "source": "chariloto",
                            "lineup": lineup_info["lineup"],
                            "line_count": lineup_info.get("line_count")
                        })

                    odds_data = fetch_odds(chari_client, open_day, vel_code, r_num, snapshot_label="morning")
                    for od in odds_data:
                        od["open_day"] = open_day
                        od["vel_code"] = vel_code
                        od["race_num"] = r_num
                        o_rows.append(od)

                    if "netkeirin" in sources and "netkeirin" not in skipped_sources:
                        try:
                            race_id = make_race_id(open_day, vel_code, r_num)
                            nk_entry_data = fetch_nk_entry(polite_client, race_id)
                            honshi = nk_entry_data.get("honshi") or {}
                            honshi_op = honshi.get("opinion")
                            honshi_bets = ",".join(honshi.get("bets", [])) if honshi.get("bets") else None

                            for item in nk_entry_data.get("entries", []):
                                item["open_day"] = open_day
                                item["vel_code"] = vel_code
                                item["race_num"] = r_num
                                item["honshi_opinion"] = honshi_op
                                item["honshi_bets"] = honshi_bets
                                nk_e_rows.append(item)

                            if nk_entry_data.get("lineup"):
                                l_rows.append({
                                    "open_day": open_day,
                                    "vel_code": vel_code,
                                    "race_num": r_num,
                                    "source": "netkeirin",
                                    "lineup": nk_entry_data["lineup"]
                                })

                            nk_cmts = fetch_nk_comments(polite_client, race_id)
                            for c in nk_cmts:
                                c["open_day"] = open_day
                                c["vel_code"] = vel_code
                                c["race_num"] = r_num
                                nk_c_rows.append(c)

                        except PoliteRateLimitError as e:
                            logger.warning(f"Netkeirin rate-limited in today-mode ({e}); skipping netkeirin for remaining run")
                            skipped_sources.add("netkeirin")
                            any_failure = True
                        except Exception as e:
                            logger.warning(f"Netkeirin failed in today-mode for {vel_code} {open_day} R{r_num}: {e}")

                if timed_out:
                    break

                storage.save("race", open_day, r_rows)
                storage.save("entry", open_day, e_rows)
                storage.save("prediction", open_day, p_rows)
                storage.save("odds", open_day, o_rows)
                storage.save("lineup", open_day, l_rows)
                if nk_e_rows:
                    storage.save("nk_entry", open_day, nk_e_rows)
                if nk_c_rows:
                    storage.save("nk_comment", open_day, nk_c_rows)

                logger.info(f"[Today-Mode] Processed {vel_code} {open_day} in {time.monotonic()-vd_start:.2f}s")
            except StorageError as e:
                logger.error(f"[Today-Mode] Storage error on {vel_code} {open_day}: {e}")
                any_failure = True
                break
            except ChariRateLimitError as e:
                logger.warning(f"[Today-Mode] Chariloto rate-limited on {vel_code} {open_day}: {e}; skipping chariloto")
                skipped_sources.add("chariloto")
                any_failure = True
            except Exception as e:
                logger.error(f"[Today-Mode] Error processing {vel_code} {open_day}: {e}")
                check_error_threshold()
            continue

        # Normal Mode
        # 1. Chariloto
        if "chariloto" in sources and not storage.is_done(vel_code, open_day, "chariloto"):
            if "chariloto" in skipped_sources:
                any_failure = True
            else:
                try:
                    res_list, payout_list, lineup_list = fetch_results_day(chari_client, vel_code, open_day)
                    consecutive_errors = 0

                    meeting, races_menu = fetch_vel_menu(chari_client, vel_code, open_day)

                    if _is_confirmed_no_races(meeting, races_menu) and not res_list:
                        logger.info(f"Confirmed no races for Chariloto {vel_code} {open_day}")
                        storage.add_done(vel_code, open_day, "chariloto")
                    else:
                        menu_races = [r for r in races_menu if r.get("race_num")]
                        if not menu_races:
                            raise ValueError(f"Empty vel_menu races for Chariloto on scheduled day {vel_code} {open_day}")

                        expected_r_nums = sorted(list(set(
                            r["race_num"] for r in menu_races if not _is_race_cancelled(r)
                        )))
                        r_num_set = set(r["race_num"] for r in res_list if r.get("race_num"))

                        if not expected_r_nums and not res_list:
                            logger.info(f"All menu races cancelled for Chariloto {vel_code} {open_day}")
                            storage.add_done(vel_code, open_day, "chariloto")
                        else:
                            if not res_list or not r_num_set:
                                raise ValueError(f"Empty Chariloto results for scheduled day {vel_code} {open_day}")

                            missing_results = [rn for rn in expected_r_nums if rn not in r_num_set]
                            if missing_results:
                                raise ValueError(
                                    f"Incomplete Chariloto results for {vel_code} {open_day}: missing races {missing_results}"
                                )

                            r_rows, e_rows, p_rows, o_rows = [], [], [], []
                            races_to_fetch = sorted(list(set(expected_r_nums) | r_num_set))

                            for r_num in races_to_fetch:
                                if time.monotonic() >= deadline:
                                    logger.info("Reached max execution time limit inside Chariloto race loop. Stopping safely.")
                                    timed_out = True
                                    break

                                r_info, entries, pred = fetch_race_info(chari_client, open_day, vel_code, r_num)
                                if not r_info or (r_num in expected_r_nums and not entries):
                                    raise ValueError(
                                        f"Empty Chariloto race/entry rows for {vel_code} {open_day} R{r_num}"
                                    )
                                r_info.update(meeting)
                                r_rows.append(r_info)

                                for e in entries:
                                    e["open_day"] = open_day
                                    e["vel_code"] = vel_code
                                    e["race_num"] = r_num
                                    e_rows.append(e)

                                pred["open_day"] = open_day
                                pred["vel_code"] = vel_code
                                pred["race_num"] = r_num
                                p_rows.append(pred)

                                odds_data = fetch_odds(chari_client, open_day, vel_code, r_num, snapshot_label=args.snapshot)
                                for od in odds_data:
                                    od["open_day"] = open_day
                                    od["vel_code"] = vel_code
                                    od["race_num"] = r_num
                                    o_rows.append(od)

                            if timed_out:
                                break

                            if not r_rows or not e_rows:
                                raise ValueError(f"Empty Chariloto race/entry rows for scheduled day {vel_code} {open_day}")

                            for l in lineup_list:
                                l["source"] = "chariloto"

                            storage.save("race", open_day, r_rows)
                            storage.save("entry", open_day, e_rows)
                            storage.save("prediction", open_day, p_rows)
                            storage.save("odds", open_day, o_rows)
                            storage.save("result", open_day, res_list)
                            storage.save("payout", open_day, payout_list)
                            storage.save("lineup", open_day, lineup_list)

                            storage.add_done(vel_code, open_day, "chariloto")
                except StorageError as e:
                    logger.error(f"Storage error during Chariloto save for {vel_code} {open_day}: {e}")
                    any_failure = True
                    break
                except ChariRateLimitError as e:
                    logger.warning(f"Chariloto rate-limited for {vel_code} {open_day}: {e}; skipping chariloto until next run")
                    skipped_sources.add("chariloto")
                    any_failure = True
                except Exception as e:
                    logger.error(f"Chariloto failed for {vel_code} {open_day}: {e}")
                    check_error_threshold()

        if timed_out:
            break

        # 2. Netkeirin
        if "netkeirin" in sources and not storage.is_done(vel_code, open_day, "netkeirin"):
            if "netkeirin" in skipped_sources or "chariloto" in skipped_sources:
                any_failure = True
            else:
                try:
                    meeting, races_menu = fetch_vel_menu(chari_client, vel_code, open_day)
                    menu_races = [r for r in races_menu if r.get("race_num")]

                    if _is_confirmed_no_races(meeting, races_menu):
                        logger.info(f"Confirmed no races for Netkeirin {vel_code} {open_day}")
                        storage.add_done(vel_code, open_day, "netkeirin")
                    else:
                        if not menu_races:
                            raise ValueError(f"Empty vel_menu races for Netkeirin on scheduled day {vel_code} {open_day}")

                        expected_r_nums = [r["race_num"] for r in menu_races if not _is_race_cancelled(r)]
                        if not expected_r_nums:
                            logger.info(f"All menu races cancelled for Netkeirin {vel_code} {open_day}")
                            storage.add_done(vel_code, open_day, "netkeirin")
                        else:
                            nk_e_rows, nk_c_rows, nk_l_rows = [], [], []
                            for r_num in expected_r_nums:
                                if time.monotonic() >= deadline:
                                    logger.info("Reached max execution time limit inside Netkeirin race loop. Stopping safely.")
                                    timed_out = True
                                    break

                                race_id = make_race_id(open_day, vel_code, r_num)
                                nk_entry_data = fetch_nk_entry(polite_client, race_id)
                                race_entries = nk_entry_data.get("entries") or []
                                if not race_entries:
                                    raise ValueError(f"Empty netkeirin entries for {vel_code} {open_day} R{r_num}")

                                honshi = nk_entry_data.get("honshi") or {}
                                honshi_op = honshi.get("opinion")
                                honshi_bets = ",".join(honshi.get("bets", [])) if honshi.get("bets") else None

                                for item in race_entries:
                                    item["open_day"] = open_day
                                    item["vel_code"] = vel_code
                                    item["race_num"] = r_num
                                    item["honshi_opinion"] = honshi_op
                                    item["honshi_bets"] = honshi_bets
                                    nk_e_rows.append(item)

                                if nk_entry_data.get("lineup"):
                                    nk_l_rows.append({
                                        "open_day": open_day,
                                        "vel_code": vel_code,
                                        "race_num": r_num,
                                        "source": "netkeirin",
                                        "lineup": nk_entry_data["lineup"]
                                    })

                                nk_cmts = fetch_nk_comments(polite_client, race_id)
                                for c in nk_cmts:
                                    c["open_day"] = open_day
                                    c["vel_code"] = vel_code
                                    c["race_num"] = r_num
                                    nk_c_rows.append(c)

                            if timed_out:
                                break

                            if not nk_e_rows:
                                raise ValueError(f"Empty netkeirin entries for scheduled day {vel_code} {open_day}")

                            consecutive_errors = 0
                            storage.save("nk_entry", open_day, nk_e_rows)
                            storage.save("nk_comment", open_day, nk_c_rows)
                            if nk_l_rows:
                                storage.save("lineup", open_day, nk_l_rows)

                            storage.add_done(vel_code, open_day, "netkeirin")
                except StorageError as e:
                    logger.error(f"Storage error during Netkeirin save for {vel_code} {open_day}: {e}")
                    any_failure = True
                    break
                except ChariRateLimitError as e:
                    logger.warning(f"Chariloto rate-limited during Netkeirin menu fetch for {vel_code} {open_day}: {e}")
                    skipped_sources.add("chariloto")
                    any_failure = True
                except PoliteRateLimitError as e:
                    logger.warning(f"Netkeirin rate-limited for {vel_code} {open_day}: {e}; skipping netkeirin until next run")
                    skipped_sources.add("netkeirin")
                    any_failure = True
                except Exception as e:
                    # netkeirin は補助の取得元。一時的に失敗しても主力（チャリロト）を止めない。
                    # 取得済みにしないので次の実行でやり直す（Claude修正、2026-11-20）
                    logger.error(f"Netkeirin failed for {vel_code} {open_day}: {e}")
                    any_failure = True

        if timed_out:
            break

        # 3. Weather
        if "weather" in sources and not storage.is_done(vel_code, open_day, "weather"):
            if "weather" in skipped_sources:
                any_failure = True
            else:
                try:
                    if time.monotonic() >= deadline:
                        logger.info("Reached max execution time limit before weather fetch. Stopping safely.")
                        timed_out = True
                        break

                    is_indoor = venue_info.get("is_indoor", False)
                    if is_indoor:
                        storage.add_done(vel_code, open_day, "weather")
                    else:
                        weather_rows = []
                        jma_station = venue_info.get("jma")  # venues.json のキー名に合わせた（Claude修正）
                        jma_success = False

                        if jma_station and "jma" not in skipped_sources:
                            try:
                                w_data = fetch_jma_10min(polite_client, jma_station, open_day)
                                if not w_data:
                                    raise ValueError("Empty JMA 10min weather result")
                                for w in w_data:
                                    w["time"] = normalize_jma_time(open_day, str(w.get("time", "")))
                                    w["open_day"] = open_day
                                    w["vel_code"] = vel_code
                                    w["source"] = "jma"
                                    weather_rows.append(w)
                                jma_success = True
                            except PoliteRateLimitError as e:
                                logger.warning(f"JMA rate-limited for {vel_code} {open_day}: {e}; skipping JMA")
                                skipped_sources.add("jma")
                            except Exception as e:
                                logger.warning(f"JMA weather failed for {vel_code} {open_day}: {e}")

                        if not jma_success:
                            lat = venue_info.get("lat")
                            lon = venue_info.get("lon")
                            if lat is not None and lon is not None and "openmeteo" not in skipped_sources:
                                try:
                                    om_data = fetch_openmeteo_hourly(polite_client, lat, lon, open_day, open_day)
                                    for w in om_data:
                                        w["open_day"] = open_day
                                        w["vel_code"] = vel_code
                                        w["source"] = "openmeteo"
                                        weather_rows.append(w)
                                except PoliteRateLimitError as e:
                                    logger.warning(f"OpenMeteo rate-limited for {vel_code} {open_day}: {e}; skipping OpenMeteo")
                                    skipped_sources.add("openmeteo")
                                except Exception as e:
                                    logger.warning(f"OpenMeteo weather failed for {vel_code} {open_day}: {e}")

                        if weather_rows:
                            consecutive_errors = 0
                            storage.save("weather", open_day, weather_rows)
                            storage.add_done(vel_code, open_day, "weather")
                        else:
                            logger.warning(f"No weather rows for {vel_code} {open_day}; will retry next run")
                            any_failure = True

                except StorageError as e:
                    logger.error(f"Storage error during weather save for {vel_code} {open_day}: {e}")
                    any_failure = True
                    break
                except Exception as e:
                    logger.error(f"Weather processing failed for {vel_code} {open_day}: {e}")
                    check_error_threshold()

        logger.info(f"Finished {vel_code} {open_day} in {time.monotonic()-vd_start:.2f}s")

    # 期間内の月を最後まで取り終え、すべての対象場・対象日・対象ソースが完了していれば「月完了」の印を付ける
    if not args.today_mode and not timed_out and not any_failure:
        active_venues = calendar_active_venues or target_venues
        cfg_range_start: Optional[str] = None
        cfg_range_end: Optional[str] = None
        range_path = pathlib.Path("config/backfill_range.txt")
        if range_path.exists():
            try:
                rs, re_ = range_path.read_text(encoding="utf-8").strip().split(",")
                cfg_range_start, cfg_range_end = rs.strip(), re_.strip()
            except Exception:
                pass

        for yr, mo in ym_range:
            ym = f"{yr:04d}-{mo:02d}"
            month_start = f"{ym}-01"
            next_month = f"{yr + (mo == 12):04d}-{(mo % 12) + 1:02d}-01"
            month_end = (datetime.strptime(next_month, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            eff_start = max(month_start, cfg_range_start) if (cfg_range_start and cfg_range_start.startswith(ym)) else month_start
            eff_end = min(month_end, cfg_range_end) if (cfg_range_end and cfg_range_end.startswith(ym)) else month_end

            # 期間がその月の対象範囲（端の月は backfill_range 内の日付）をすべて含み、全開催日・全ソースが完了しているときだけ印を付ける
            if args.start <= eff_start and args.end >= eff_end and ym not in storage.unreadable_months:
                all_venues_fetched = bool(active_venues) and all(
                    (v, yr) in venue_year_days_cache for v in active_venues
                )
                month_days = [
                    (v, d)
                    for v in sorted(active_venues)
                    for d in venue_year_days_cache.get((v, yr), [])
                    if d.startswith(ym) and eff_start <= d <= eff_end
                ]
                if (
                    all_venues_fetched
                    and bool(month_days)
                    and bool(sources)
                    and all(storage.is_done(v, d, src) for v, d in month_days for src in sources)
                ):
                    storage.add_done("*", month_start, "month_complete")

    # この実行で新しく取れた数をファイルに残す（0なら backfill-auto は自分を再起動しない＝空回り防止）
    try:
        pathlib.Path("collect_progress.txt").write_text(str(ParquetStorage.new_done_count))
    except Exception:
        pass
    logger.info(f"New done entries this run: {ParquetStorage.new_done_count}")
    logger.info("=== Final Request Summary ===")
    logger.info(f"Chariloto Total Requests: {chari_client.request_count}")
    logger.info(f"Polite Total Requests: {polite_client.request_count}")


if __name__ == "__main__":
    main()
