import argparse
import json
import logging
import pathlib
import sys
import time
from datetime import datetime, timedelta
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
from keirin.fetch.client import ChariClient, ChariError
from keirin.fetch.http import PoliteClient, PoliteError
from keirin.fetch.jma import fetch_jma_10min
from keirin.fetch.netkeirin import fetch_nk_comments, fetch_nk_entry, make_race_id
from keirin.fetch.openmeteo import fetch_openmeteo_hourly
from keirin.storage import ParquetStorage

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Keirin data collector script")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--venues", type=str, default="", help="Comma separated venue codes or names")
    parser.add_argument("--sources", type=str, default="chariloto,netkeirin,weather", help="Comma separated sources")
    parser.add_argument("--max-minutes", type=int, default=330, help="Max execution time in minutes")
    parser.add_argument("--data-dir", type=str, default="data", help="Data directory path")
    parser.add_argument("--snapshot", type=str, default="final", help="Odds snapshot label")
    parser.add_argument("--today-mode", action="store_true", help="Mode for fetching today's upcoming races")

    args = parser.parse_args()

    start_time = time.time()
    max_seconds = args.max_minutes * 60

    storage = ParquetStorage(data_dir=args.data_dir)
    chari_client = ChariClient()
    polite_client = PoliteClient()

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
            logger.warning(f"Failed to fetch calendar for {yr}-{mo:02d}: {e}")
            check_error_threshold()

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

    for vel_code, open_day in sorted_schedule:
        if time.time() - start_time > max_seconds:
            logger.info("Reached max execution time limit. Stopping execution safely.")
            timed_out = True
            break

        vd_start = time.time()
        venue_info = venues_config.get(vel_code, {})

        if args.today_mode:
            try:
                meeting, races_menu = fetch_vel_menu(chari_client, vel_code, open_day)
                consecutive_errors = 0

                r_rows, e_rows, p_rows, o_rows, l_rows = [], [], [], [], []
                nk_e_rows, nk_c_rows = [], []

                for r_item in races_menu:
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

                    if "netkeirin" in sources:
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

                        except Exception as e:
                            logger.warning(f"Netkeirin failed in today-mode for {vel_code} {open_day} R{r_num}: {e}")

                storage.save("race", open_day, r_rows)
                storage.save("entry", open_day, e_rows)
                storage.save("prediction", open_day, p_rows)
                storage.save("odds", open_day, o_rows)
                storage.save("lineup", open_day, l_rows)
                if nk_e_rows:
                    storage.save("nk_entry", open_day, nk_e_rows)
                if nk_c_rows:
                    storage.save("nk_comment", open_day, nk_c_rows)

                logger.info(f"[Today-Mode] Processed {vel_code} {open_day} in {time.time()-vd_start:.2f}s")
            except Exception as e:
                logger.error(f"[Today-Mode] Error processing {vel_code} {open_day}: {e}")
                check_error_threshold()
            continue

        # Normal Mode
        # 1. Chariloto
        if "chariloto" in sources and not storage.is_done(vel_code, open_day, "chariloto"):
            try:
                res_list, payout_list, lineup_list = fetch_results_day(chari_client, vel_code, open_day)
                consecutive_errors = 0

                meeting, _ = fetch_vel_menu(chari_client, vel_code, open_day)

                r_num_set = set(r["race_num"] for r in res_list if r.get("race_num"))
                r_rows, e_rows, p_rows, o_rows = [], [], [], []

                for r_num in sorted(list(r_num_set)):
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

                    odds_data = fetch_odds(chari_client, open_day, vel_code, r_num, snapshot_label=args.snapshot)
                    for od in odds_data:
                        od["open_day"] = open_day
                        od["vel_code"] = vel_code
                        od["race_num"] = r_num
                        o_rows.append(od)

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
            except Exception as e:
                logger.error(f"Chariloto failed for {vel_code} {open_day}: {e}")
                check_error_threshold()

        # 2. Netkeirin
        if "netkeirin" in sources and not storage.is_done(vel_code, open_day, "netkeirin"):
            try:
                meeting, races_menu = fetch_vel_menu(chari_client, vel_code, open_day)
                r_nums = [r["race_num"] for r in races_menu if r.get("race_num")]

                nk_e_rows, nk_c_rows, nk_l_rows = [], [], []
                for r_num in r_nums:
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

                consecutive_errors = 0
                storage.save("nk_entry", open_day, nk_e_rows)
                storage.save("nk_comment", open_day, nk_c_rows)
                if nk_l_rows:
                    storage.save("lineup", open_day, nk_l_rows)

                storage.add_done(vel_code, open_day, "netkeirin")
            except Exception as e:
                # netkeirin は補助の取得元。一時的に失敗しても主力（チャリロト）を止めない。
                # 取得済みにしないので次の実行でやり直す（Claude修正、2026-11-20）
                logger.error(f"Netkeirin failed for {vel_code} {open_day}: {e}")
                any_failure = True

        # 3. Weather
        if "weather" in sources and not storage.is_done(vel_code, open_day, "weather"):
            try:
                is_indoor = venue_info.get("is_indoor", False)
                if is_indoor:
                    storage.add_done(vel_code, open_day, "weather")
                else:
                    weather_rows = []
                    jma_station = venue_info.get("jma")  # venues.json のキー名に合わせた（Claude修正）
                    jma_success = False

                    if jma_station:
                        try:
                            w_data = fetch_jma_10min(polite_client, jma_station, open_day)
                            for w in w_data:
                                w["open_day"] = open_day
                                w["vel_code"] = vel_code
                                w["source"] = "jma"
                                weather_rows.append(w)
                            jma_success = True
                        except Exception as e:
                            logger.warning(f"JMA weather failed for {vel_code} {open_day}: {e}")

                    if not jma_success:
                        lat = venue_info.get("lat")
                        lon = venue_info.get("lon")
                        if lat is not None and lon is not None:
                            try:
                                om_data = fetch_openmeteo_hourly(polite_client, lat, lon, open_day, open_day)
                                for w in om_data:
                                    w["open_day"] = open_day
                                    w["vel_code"] = vel_code
                                    w["source"] = "openmeteo"
                                    weather_rows.append(w)
                            except Exception as e:
                                logger.warning(f"OpenMeteo weather failed for {vel_code} {open_day}: {e}")

                    if weather_rows:
                        consecutive_errors = 0
                        storage.save("weather", open_day, weather_rows)
                        storage.add_done(vel_code, open_day, "weather")
                    else:
                        logger.warning(f"No weather rows for {vel_code} {open_day}; will retry next run")

            except Exception as e:
                logger.error(f"Weather processing failed for {vel_code} {open_day}: {e}")
                check_error_threshold()

        logger.info(f"Finished {vel_code} {open_day} in {time.time()-vd_start:.2f}s")

    # 期間内の月を最後まで取り終え、失敗もなければ「月完了」の印を付ける（backfill-auto が次の月へ進むのに使う）
    if not args.today_mode and not timed_out and not any_failure:
        for yr, mo in ym_range:
            month_start = f"{yr:04d}-{mo:02d}-01"
            next_month = f"{yr + (mo == 12):04d}-{(mo % 12) + 1:02d}-01"
            # 期間がその月を丸ごと含むときだけ印を付ける
            if args.start <= month_start and args.end >= (datetime.strptime(next_month, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"):
                storage.add_done("*", month_start, "month_complete")

    logger.info("=== Final Request Summary ===")
    logger.info(f"Chariloto Total Requests: {chari_client.request_count}")
    logger.info(f"Polite Total Requests: {polite_client.request_count}")


if __name__ == "__main__":
    main()
