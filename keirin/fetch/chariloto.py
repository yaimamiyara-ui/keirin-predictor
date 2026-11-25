import json
import logging
import re
from typing import Any, Optional, Tuple
from bs4 import BeautifulSoup
from keirin.fetch.client import ChariClient

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 共通変換関数・ヘルパー
# ----------------------------------------------------------------------

def _to_half_width(text: str) -> str:
    if not text:
        return ""
    # 全角英数・記号を半角に変換
    return text.translate(str.maketrans(
        '０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ　',
        '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz '
    ))


def _clean_str(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    cleaned = _to_half_width(text).strip()
    # 内部の余分な空白も除去
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned if cleaned != "" else None


def _remove_all_whitespace(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    s = _to_half_width(text)
    s = re.sub(r'\s+', '', s)
    return s if s != "" else None


def _to_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    if isinstance(val, int):
        return val
    try:
        s = _to_half_width(str(val))
        s = re.sub(r'[^\d-]', '', s)
        return int(s) if s != "" else None
    except Exception:
        return None


def _to_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    if isinstance(val, (float, int)):
        return float(val)
    try:
        s = _to_half_width(str(val))
        s = re.sub(r'[^\d.-]', '', s)
        return float(s) if s != "" else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# 純粋 parse 関数 & fetch 関数
# ----------------------------------------------------------------------

# 1. 開催日一覧
def parse_venue_year_days(html: str, vel: str, year: int) -> list[str]:
    pattern = rf'/keirin/results/{vel}/({year}-\d{{2}}-\d{{2}})'
    matches = re.findall(pattern, html)
    days = sorted(list(set(matches)))
    return days


def fetch_venue_year_days(client: ChariClient, vel: str, year: int) -> list[str]:
    path = f"/keirin/results/{vel}/"
    html = client.get_html(path, params={"year": year})
    return parse_venue_year_days(html, vel, year)


# 2. 開催カレンダー（全場・月単位）
def parse_calendar(html: str, year: int, month: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    
    calendar_box = soup.find("div", id="calendar-keirin") or soup
    tables = calendar_box.find_all("table", class_="calendar-table")
    
    for table in tables:
        # 日付ヘッダー取得
        dates = []
        thead = table.find("thead")
        if thead:
            for th in thead.find_all("th"):
                d_val = _to_int(th.get_text())
                if d_val is not None:
                    dates.append(d_val)
        
        tbody = table.find("tbody")
        if not tbody:
            continue
            
        for tr in tbody.find_all("tr"):
            name_td = tr.find("td", class_="cell-name")
            if not name_td:
                continue
            venue_name = _remove_all_whitespace(name_td.get_text())
            
            date_tds = tr.find_all("td", class_="cell-date")
            for idx, td in enumerate(date_tds):
                day = dates[idx] if idx < len(dates) else (idx + 1)
                
                # イベント情報の解析
                event_info = td.find("div", class_="event-info")
                if not event_info and "has-basic" not in td.get("class", []):
                    continue
                
                grade = None
                flags = []
                
                if event_info:
                    grade_p = event_info.find("p", class_="grade-name")
                    if grade_p:
                        grade_tag = grade_p.find("span", class_="tag")
                        if grade_tag:
                            grade = _clean_str(grade_tag.get_text())
                    
                    # flags: N, MD, Mo, G など
                    tags = event_info.find_all("span", class_="tag")
                    for t in tags:
                        txt = _clean_str(t.get_text())
                        if txt and txt not in (grade, None):
                            flags.append(txt)
                
                if grade or flags or "has-basic" in td.get("class", []):
                    results.append({
                        "venue_name": venue_name,
                        "day": day,
                        "grade": grade,
                        "flags": flags
                    })
                    
    return results


def fetch_calendar(client: ChariClient, year: int, month: int) -> list[dict]:
    path = f"/keirin/calendars/{year}/{month:02d}/grade"
    html = client.get_html(path)
    return parse_calendar(html, year, month)


# 3. その日のレース一覧
def parse_vel_menu(json_data: dict) -> Tuple[dict, list[dict]]:
    header_raw = json_data.get("header", {})
    meeting = {
        "vel_name": header_raw.get("vel_name"),
        "open_day": header_raw.get("open_day"),
        "open_grade_code": header_raw.get("open_grade_code"),
        "race_name": header_raw.get("race_name"),
        "spent_days_string": header_raw.get("spent_days_string"),
        "night_race_type": header_raw.get("night_race_type"),
        "sub_name": header_raw.get("sub_name"),
        "is_girls": header_raw.get("is_girls"),
        "rehold_string": header_raw.get("rehold_string")
    }
    
    races = []
    for r in json_data.get("races", []):
        races.append({
            "race_num": _to_int(r.get("race_num")),
            "is_teppan": r.get("is_teppan"),
            "sale_to_at": r.get("sale_to_at"),
            "event_name": _remove_all_whitespace(r.get("event_name"))
        })
        
    return meeting, races


def fetch_vel_menu(client: ChariClient, vel_code: str, open_day: str) -> Tuple[dict, list[dict]]:
    path = "/api/keirin/vel_menu"
    params = {"vel_code": vel_code, "race_num": 1, "open_day": open_day}
    json_data = client.get_json(path, params=params)
    return parse_vel_menu(json_data)


# 4. 出走表（レース単位）
def parse_race_info(json_data: dict) -> Tuple[dict, list[dict], dict]:
    box = json_data.get("box", {})
    status = json_data.get("race_status", {})
    program = json_data.get("race_program", {})
    athletes_map = json_data.get("race_athletes", {})
    prediction_raw = json_data.get("prediction", {})
    
    race = {
        "open_day": box.get("open_day"),
        "vel_code": box.get("vel_code"),
        "race_num": _to_int(box.get("race_num")),
        "race_name": box.get("race_name"),
        "sub_name": box.get("sub_name"),
        "grade": box.get("open_grade_code"),
        "spent_days_string": box.get("spent_days_string"),
        "night_race_type": box.get("night_race_type"),
        "is_girls": box.get("is_girls"),
        "sale_to_at": box.get("sale_to_at"),
        "race_start_at": box.get("race_start_at"),
        "distance": _to_int(program.get("distance")),
        "class_name": _remove_all_whitespace(program.get("name")),
        "event_name": _remove_all_whitespace(program.get("event_name")),
        "entry_row": _to_int(status.get("entry_row")),
        "bet_niwakufuku_sale_status": status.get("bet_niwakufuku_sale_status"),
        "bet_niwakutan_sale_status": status.get("bet_niwakutan_sale_status"),
        "bet_nirenfuku_sale_status": status.get("bet_nirenfuku_sale_status"),
        "bet_nirentan_sale_status": status.get("bet_nirentan_sale_status"),
        "bet_wide_sale_status": status.get("bet_wide_sale_status"),
        "bet_sanrenfuku_sale_status": status.get("bet_sanrenfuku_sale_status"),
        "bet_sanrentan_sale_status": status.get("bet_sanrentan_sale_status"),
        "bet_niwakufuku_result_status": status.get("bet_niwakufuku_result_status"),
        "bet_niwakutan_result_status": status.get("bet_niwakutan_result_status"),
        "bet_nirenfuku_result_status": status.get("bet_nirenfuku_result_status"),
        "bet_nirentan_result_status": status.get("bet_nirentan_result_status"),
        "bet_wide_result_status": status.get("bet_wide_result_status"),
        "bet_sanrenfuku_result_status": status.get("bet_sanrenfuku_result_status"),
        "bet_sanrentan_result_status": status.get("bet_sanrentan_result_status"),
    }
    
    entries = []
    for k, ath in athletes_map.items():
        if not ath:
            continue
        
        # average_point (88750 -> 88.75)
        avg_pt_raw = ath.get("average_point")
        avg_pt = (_to_float(avg_pt_raw) / 1000.0) if avg_pt_raw is not None else None
        
        # history data
        histories = []
        for h_key in ("history_data1", "history_data2", "history_data3"):
            if h_key in ath:
                histories.append(ath[h_key])
        history_str = json.dumps(histories, ensure_ascii=False) if histories else None
        
        entry = {
            "entry_num": _to_int(ath.get("entry_num")),
            "waku_num": _to_int(ath.get("waku_num")),
            "player_id": _to_int(ath.get("un_id")),
            "name": _remove_all_whitespace(ath.get("name")),
            "age": _to_int(ath.get("age")),
            "class_name": _clean_str(ath.get("class_name")),  # 全角→半角（Claude修正）
            "group_name": _clean_str(ath.get("group_name")),
            "pref_name": _remove_all_whitespace(ath.get("pref_name")),
            "graduate_period": _to_int(ath.get("graduate_period")),
            "gear_ratio": _to_float(ath.get("gear_ratio")),
            "gear_ratio_before": _to_float(ath.get("gear_ratio_before")),
            "average_point": avg_pt,
            "average_win_raw": ath.get("average_win"),
            "average_rentai_raw": ath.get("average_rentai"),
            "nige_count": _to_int(ath.get("nige_count")),
            "sashi_count": _to_int(ath.get("sashi_count")),
            "makuri_count": _to_int(ath.get("makuri_count")),
            "mark_count": _to_int(ath.get("mark_count")),
            "standing_count": _to_int(ath.get("standing_count")),
            "back_count": _to_int(ath.get("back_count")),
            "leg_quality": ath.get("leg_quality"),
            "first_count": _to_int(ath.get("first_count")),
            "second_count": _to_int(ath.get("second_count")),
            "third_count": _to_int(ath.get("third_count")),
            "out_rank_count": _to_int(ath.get("out_rank_count")),
            "win_count": _to_int(ath.get("win_count")),
            "renshou_count": _to_int(ath.get("renshou_count")),
            "prize": _to_int(ath.get("prize")),
            "rank": _to_int(ath.get("rank")),
            "last3d": _to_int(ath.get("last3d")),
            "comment": ath.get("comment"),
            "history": history_str
        }
        entries.append(entry)
        
    entries.sort(key=lambda x: x["entry_num"] or 0)
    
    # prediction marks (e.g. "1:◎,3:○")
    marks_list = prediction_raw.get("marks", [])
    marks_str_list = []
    if isinstance(marks_list, list):
        for idx, m in enumerate(marks_list, start=1):
            if m:
                marks_str_list.append(f"{idx}:{m}")
    marks_str = ",".join(marks_str_list) if marks_str_list else None
    
    seqs = prediction_raw.get("sequences", [])
    seqs_str = "|".join(seqs) if isinstance(seqs, list) and seqs else None
    
    prediction = {
        "marks": marks_str,
        "sequences": seqs_str,
        "comment": prediction_raw.get("comment")
    }
    
    return race, entries, prediction


def fetch_race_info(client: ChariClient, open_day: str, vel_code: str, race_num: int) -> Tuple[dict, list[dict], dict]:
    path = "/api/keirin/race_info"
    params = {"open_day": open_day, "vel_code": vel_code, "race_num": race_num}
    json_data = client.get_json(path, params=params)
    return parse_race_info(json_data)


# 5. オッズ
def parse_odds(json_data: dict, snapshot_label: str = "") -> list[dict]:
    odds_list = json_data.get("odds", [])
    results = []
    
    for item in odds_list:
        bet_type = item.get("bet_type")
        combo = item.get("deme")
        rate_str = str(item.get("rate", ""))
        norm_rate_str = str(item.get("normalize_rate", ""))
        
        # 発売なし/未確定
        if rate_str == "0.0" and norm_rate_str == "9999.9":
            odds_min = None
            odds_max = None
        else:
            if "〜" in rate_str:
                parts = rate_str.split("〜")
                odds_min = _to_float(parts[0])
                odds_max = _to_float(parts[1]) if len(parts) > 1 else odds_min
            elif "-" in rate_str and not rate_str.startswith("-"): # 範囲表記の可能性
                parts = rate_str.split("-")
                odds_min = _to_float(parts[0])
                odds_max = _to_float(parts[1]) if len(parts) > 1 else odds_min
            else:
                odds_min = _to_float(rate_str)
                odds_max = odds_min
                
        results.append({
            "bet_type": bet_type,
            "combo": combo,
            "odds_min": odds_min,
            "odds_max": odds_max,
            "is_valid": item.get("is_valid"),
            "on_final": item.get("on_final"),
            "snapshot": snapshot_label
        })
        
    return results


def fetch_odds(client: ChariClient, open_day: str, vel_code: str, race_num: int, snapshot_label: str = "") -> list[dict]:
    path = "/api/keirin/odds_per_race"
    params = {"open_day": open_day, "vel_code": vel_code, "race_num": race_num}
    json_data = client.get_json(path, params=params)
    return parse_odds(json_data, snapshot_label=snapshot_label)


# 6. 並び予想
def parse_lineup(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    
    # 「周回予想」を含むエリアを探す
    lineup_table = None
    for tr in soup.find_all("tr"):
        th = tr.find("th")
        if th and "周回予想" in th.get_text():
            lineup_table = tr.find("td")
            break
            
    if not lineup_table:
        logging.warning("Lineup table not found")
        return {"lineup": "", "lines": [], "line_count": 0}

    # td 内の要素を走査
    tokens = []
    # 各 <td> から span を取得
    for td in lineup_table.find_all("td"):
        square_span = td.find("span", class_=lambda c: c and "square" in c)
        p10_span = td.find("span", class_=lambda c: c and "p10" in c)
        
        if square_span:
            num = _to_int(square_span.get_text())
            if num is not None:
                tokens.append(num)
        elif p10_span:
            tokens.append("/")
            
    # 連続する '/' の統合と末尾の '/' 削除
    lines = []
    current_line = []
    
    for token in tokens:
        if token == "/":
            if current_line:
                lines.append(current_line)
                current_line = []
        else:
            current_line.append(token)
    if current_line:
        lines.append(current_line)
        
    lineup_str = "/".join(["-".join(map(str, line)) for line in lines])
    
    return {
        "lineup": lineup_str,
        "lines": lines,
        "line_count": len(lines)
    }


def fetch_lineup(client: ChariClient, date: str, vel: str, race: int) -> dict:
    path = f"/keirin/athletes/{date}/{vel}/{race}"
    html = client.get_html(path)
    return parse_lineup(html)


# 7. 結果（1日1場のページに全レース）
def parse_results_day(html: str, vel: str, date: str) -> Tuple[list[dict], list[dict], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    
    # 第Nレース のセクションを探す
    sections = soup.find_all("section", id=re.compile(r"^race\d+"))
    if not sections:
        # 代替：第Nレース の見出しをテキストサーチ
        p_tags = soup.find_all(string=re.compile(r"第\s*\d+\s*レース"))
        if not p_tags:
            page_text = _clean_str(soup.get_text()) or ""
            if any(kw in page_text for kw in ("開催中止", "順延", "開催なし")):
                return [], [], []
            raise ValueError("No race headers found in results page (format changed).")
        # セクションが見つからなくても、親ブロック単位でまとめる
        sections = [p.find_parent(["section", "div"]) for p in p_tags if p.find_parent(["section", "div"])]

    if not sections:
        raise ValueError("No race headers found in results page (format changed).")

    all_results = []
    all_payouts = []
    all_lineups = []
    
    BET_TYPE_MAP = {
        "2枠複": "niwakufuku", "２枠複": "niwakufuku",
        "2枠単": "niwakutan", "２枠単": "niwakutan",
        "2車複": "nirenfuku", "２車複": "nirenfuku",
        "2車単": "nirentan", "２車単": "nirentan",
        "3連複": "sanrenfuku", "３連複": "sanrenfuku",
        "3連単": "sanrentan", "３連単": "sanrentan",
        "ワイド": "wide",
        # 結果ページの見出しは「2枠連」「2車連」「3連勝」＋「複/単」の形（Claude修正）
        "2枠連複": "niwakufuku", "2枠連単": "niwakutan",
        "2車連複": "nirenfuku", "2車連単": "nirentan",
        "3連勝複": "sanrenfuku", "3連勝単": "sanrentan",
    }

    for sec in sections:
        # レース番号特定
        sec_id = sec.get("id", "")
        race_num_match = re.search(r"race(\d+)", sec_id)
        if race_num_match:
            race_num = int(race_num_match.group(1))
        else:
            txt_match = re.search(r"第\s*(\d+)\s*レース", sec.get_text())
            if txt_match:
                race_num = int(txt_match.group(1))
            else:
                continue

        # --- A. 着順表解析 ---
        res_table = sec.find("table", class_=lambda c: c and "table-striped" in c)
        if res_table:
            for tr in res_table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 10:
                    continue
                
                rank_raw = _clean_str(tds[0].get_text())
                rank_num = _to_int(rank_raw)
                status = None if rank_num is not None else rank_raw
                
                car_num = _to_int(tds[1].get_text())
                name = _remove_all_whitespace(tds[2].get_text())
                age = _to_int(tds[3].get_text())
                pref = _clean_str(tds[4].get_text())
                period = _to_int(tds[5].get_text())
                class_group = _clean_str(tds[6].get_text())
                margin = _clean_str(tds[7].get_text())
                last_lap = _to_float(tds[8].get_text())
                factor = _clean_str(tds[9].get_text())
                sb = _clean_str(tds[10].get_text()) if len(tds) > 10 else None
                note = _clean_str(tds[11].get_text()) if len(tds) > 11 else None
                
                all_results.append({
                    "open_day": date,
                    "vel_code": vel,
                    "race_num": race_num,
                    "rank": rank_num,
                    "status": status,
                    "car_num": car_num,
                    "name": name,
                    "age": age,
                    "pref": pref,
                    "period": period,
                    "class_group": class_group,
                    "margin": margin,
                    "last_lap": last_lap,
                    "factor": factor,
                    "sb": sb,
                    "note": note
                })

        # --- B. 周回予想解析 ---
        lineup_info = parse_lineup(str(sec))
        if lineup_info["lineup"]:
            all_lineups.append({
                "open_day": date,
                "vel_code": vel,
                "race_num": race_num,
                "lineup": lineup_info["lineup"],
                "line_count": lineup_info["line_count"]
            })

        # --- C. 払戻解析 ---
        payout_tables = sec.find_all("table", class_=lambda c: c and "table-bordered-shade" in c)
        for ptable in payout_tables:
            th = ptable.find("th")
            if not th:
                continue
            cat_header = _clean_str(th.get_text())
            
            for tr in ptable.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                
                # パターン1: 2セル (sub_type, p_element)
                if len(tds) == 2:
                    sub_type = _clean_str(tds[0].get_text())
                    combined_cat = f"{cat_header}{sub_type}" if sub_type else cat_header
                    b_type = BET_TYPE_MAP.get(combined_cat) or BET_TYPE_MAP.get(cat_header)
                    
                    p_element = tds[1]
                    p_tags = p_element.find_all("p") or [p_element]
                    for p in p_tags:
                        text = _clean_str(p.get_text())
                        if not text or "発売無し" in text:
                            continue
                        
                        m = re.search(r"([0-9\=\-]+)\s*([\d,]+)円", text)
                        if m:
                            combo = m.group(1)
                            yen = _to_int(m.group(2))
                            if b_type and yen is not None:
                                all_payouts.append({
                                    "open_day": date,
                                    "vel_code": vel,
                                    "race_num": race_num,
                                    "bet_type": b_type,
                                    "combo": combo,
                                    "payout_yen": yen
                                })
                # パターン2: 1セル (ワイドなど)
                elif len(tds) == 1:
                    b_type = BET_TYPE_MAP.get(cat_header)
                    p_tags = tds[0].find_all("p") or [tds[0]]
                    for p in p_tags:
                        text = _clean_str(p.get_text())
                        if not text or "発売無し" in text:
                            continue
                        m = re.search(r"([0-9\=\-]+)\s*([\d,]+)円", text)
                        if m:
                            combo = m.group(1)
                            yen = _to_int(m.group(2))
                            if b_type and yen is not None:
                                all_payouts.append({
                                    "open_day": date,
                                    "vel_code": vel,
                                    "race_num": race_num,
                                    "bet_type": b_type,
                                    "combo": combo,
                                    "payout_yen": yen
                                })

    return all_results, all_payouts, all_lineups


def fetch_results_day(client: ChariClient, vel: str, date: str) -> Tuple[list[dict], list[dict], list[dict]]:
    path = f"/keirin/results/{vel}/{date}"
    html = client.get_html(path)
    return parse_results_day(html, vel, date)


# 8. 競輪場情報
def parse_velinfo(html: str, vel: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info = {
        "vel_code": vel,
        "bank_length_m": None,
        "straight_m": None,
        "center_incline": None,
        "straight_incline": None,
        "home_width_m": None,
        "back_width_m": None,
        "center_width_m": None,
        "strike_first_nige": None,
        "strike_first_makuri": None,
        "strike_first_sashi": None,
        "strike_second_nige": None,
        "strike_second_makuri": None,
        "strike_second_sashi": None,
        "strike_second_mark": None,
        "updated_at": None,
        "address": None
    }
    
    # 周長
    length_span = soup.find("div", class_="bankImagelength")
    if length_span:
        info["bank_length_m"] = _to_int(length_span.get_text())
        
    # バンクデータテーブル解析
    incline_count = 0
    for tr in soup.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if not th or not td:
            continue
        label = _clean_str(th.get_text())
        val_str = _clean_str(td.get_text())
        
        if label == "見なし直線距離":
            info["straight_m"] = _to_float(val_str)
        elif label == "センター部路面傾斜":
            incline_count += 1
            if incline_count == 1:
                info["center_incline"] = val_str
            else:
                # 3つ目の傾斜表記は実際はセンター部幅員
                info["center_width_m"] = _to_float(val_str)
        elif label == "直線部路面傾斜":
            info["straight_incline"] = val_str
        elif label == "ホーム幅員":
            info["home_width_m"] = _to_float(val_str)
        elif label == "バック幅員":
            info["back_width_m"] = _to_float(val_str)
            
    # 決まり手
    for box in soup.find_all("div", class_="velinfos-strike-box"):
        for div in box.find_all("div"):
            txt = _clean_str(div.get_text())
            if not txt:
                continue
            if "逃げ" in txt:
                val = _to_int(txt)
                if info["strike_first_nige"] is None:
                    info["strike_first_nige"] = val
                else:
                    info["strike_second_nige"] = val
            elif "捲り" in txt:
                val = _to_int(txt)
                if info["strike_first_makuri"] is None:
                    info["strike_first_makuri"] = val
                else:
                    info["strike_second_makuri"] = val
            elif "差し" in txt:
                val = _to_int(txt)
                if info["strike_first_sashi"] is None:
                    info["strike_first_sashi"] = val
                else:
                    info["strike_second_sashi"] = val
            elif "マーク" in txt:
                info["strike_second_mark"] = _to_int(txt)

    # 更新日
    for p in soup.find_all("p"):
        txt = _clean_str(p.get_text())
        if txt and "更新" in txt:
            m = re.search(r"\((.*?更新)\)", txt)
            info["updated_at"] = m.group(1) if m else txt

    # 住所
    addr_div = soup.find("div", class_="addressBox-left")
    if addr_div:
        p_first = addr_div.find("p")
        if p_first:
            info["address"] = _clean_str(p_first.get_text())

    return info


def fetch_velinfo(client: ChariClient, vel: str) -> dict:
    path = f"/velinfos/{vel}"
    html = client.get_html(path)
    return parse_velinfo(html, vel)


# 9. 選手プロフィール
def parse_athlete_profile(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    profile = {
        "player_id": None,
        "registration_num": None,
        "birth_date": None,
        "period": None,
        "class_group": None,
        "height_cm": None,
        "weight_kg": None,
        "home_bank": None,
        "pref": None
    }
    
    # URLまたは要素から player_id
    og_url = soup.find("meta", property="og:url")
    if og_url and og_url.get("content"):
        m = re.search(r"/keirin_athletes/(\d+)", og_url["content"])
        if m:
            profile["player_id"] = int(m.group(1))

    # テーブル解析
    # 1行に「見出し・値」の組が2つ並ぶことがあるので、組ごとに読む（Claude修正）
    pairs = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        for a, b in zip(cells, cells[1:]):
            if a.name == "th" and b.name == "td":
                pairs.append((_clean_str(a.get_text()), _clean_str(b.get_text())))
    for label, val in pairs:
        if label in ("登録番号", "選手登録番号"):
            profile["registration_num"] = val
        elif label == "生年月日":
            profile["birth_date"] = val
        elif label in ("登録期", "期別"):
            profile["period"] = _to_int(val)
        elif label in ("級班", "現在級班"):
            profile["class_group"] = val
        elif label == "身長":
            profile["height_cm"] = _to_int(val)
        elif label == "体重":
            profile["weight_kg"] = _to_int(val)
        elif label == "ホームバンク":
            profile["home_bank"] = val
        elif label in ("府県", "出身府県"):
            profile["pref"] = val

    return profile


def fetch_athlete_profile(client: ChariClient, player_id: int) -> dict:
    path = f"/keirin_athletes/{player_id}"
    html = client.get_html(path)
    res = parse_athlete_profile(html)
    res["player_id"] = player_id
    return res
