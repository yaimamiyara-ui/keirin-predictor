from datetime import datetime, timedelta
import re
import unicodedata
import logging
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from keirin.fetch.http import PoliteClient, PoliteError

logger = logging.getLogger(__name__)

JMA_CREDIT = "出典：気象庁ホームページ（過去の気象データ検索）を加工して作成"


def normalize_jma_time(date_str: str, time_str: str) -> str:
    """Normalize JMA observation time (e.g. '00:10' or '24:00') to JST ISO datetime 'YYYY-MM-DDTHH:MM' like Open-Meteo."""
    s = _clean_text(time_str)
    if not s:
        return s
    if "T" in s:
        return s
    parts = s.split(":")
    if len(parts) != 2:
        return f"{date_str}T{s}"
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return f"{date_str}T{s}"
    if hour == 24:
        next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        return f"{next_day}T00:{minute:02d}"
    return f"{date_str}T{hour:02d}:{minute:02d}"


def _clean_text(val: Optional[str]) -> str:
    if val is None:
        return ""
    return unicodedata.normalize("NFKC", str(val)).strip()


def _clean_val(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    s = _clean_text(val)
    if s in ("--", "×", "", "]", ")", "-", "///"):
        return None
    s = re.sub(r"[^\d\.-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def parse_jma_prec_list(html: str) -> List[int]:
    matches = re.findall(r"prec_no=(\d+)", html)
    prec_nos = sorted(list(set(map(int, matches))))
    return prec_nos


def parse_jma_stations(html: str, prec_no: int) -> List[Dict[str, Any]]:
    pattern = r"viewPoint\('([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'"
    matches = re.findall(pattern, html)

    stations = []
    seen_blocks = set()

    for m in matches:
        kind = m[0]
        block_no = m[1]
        name = _clean_text(m[2])
        kana = _clean_text(m[3])

        if block_no in seen_blocks:
            continue
        seen_blocks.add(block_no)

        try:
            lat_deg = float(m[4])
            lat_min = float(m[5])
            lat = lat_deg + lat_min / 60.0

            lon_deg = float(m[6])
            lon_min = float(m[7])
            lon = lon_deg + lon_min / 60.0
        except ValueError:
            logger.warning(f"Could not parse lat/lon for station {name} ({block_no})")
            continue

        has_wind = (m[10] == "1")
        end_year = m[15]
        end_month = m[16]
        end_day = m[17]

        end_date = None
        if end_year != "9999":
            end_date = f"{end_year}-{end_month.zfill(2)}-{end_day.zfill(2)}"

        stations.append({
            "kind": kind,
            "block_no": block_no,
            "name": name,
            "kana": kana,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "has_wind": has_wind,
            "end_date": end_date,
            "prec_no": prec_no,
        })

    return stations


def parse_jma_10min(html: str, kind: str, date_str: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tablefix1") or soup.find("table", class_=re.compile(r"data2_s"))
    if not table:
        raise ValueError("JMA 10min data table not found")

    rows = table.find_all("tr", class_="mtx")
    header_rows = []
    data_rows = []

    for r in rows:
        if r.find("th"):
            header_rows.append(r)
        elif r.find("td"):
            data_rows.append(r)

    col_time = 0
    col_precip = 3 if kind == "s" else 1
    col_temp = 4 if kind == "s" else 2
    col_wind_spd = 6 if kind == "s" else 4
    col_wind_dir = 7 if kind == "s" else 5
    col_gust_spd = 8 if kind == "s" else 6
    col_gust_dir = 9 if kind == "s" else 7

    results = []
    for r in data_rows:
        tds = r.find_all("td")
        if not tds:
            continue

        time_str = _clean_text(tds[col_time].get_text()) if len(tds) > col_time else ""
        if not time_str or ":" not in time_str:
            continue

        precip = _clean_val(tds[col_precip].get_text()) if len(tds) > col_precip else None
        temp = _clean_val(tds[col_temp].get_text()) if len(tds) > col_temp else None

        w_spd_raw = _clean_text(tds[col_wind_spd].get_text()) if len(tds) > col_wind_spd else ""
        w_dir_raw = _clean_text(tds[col_wind_dir].get_text()) if len(tds) > col_wind_dir else ""
        g_spd_raw = _clean_text(tds[col_gust_spd].get_text()) if len(tds) > col_gust_spd else ""
        g_dir_raw = _clean_text(tds[col_gust_dir].get_text()) if len(tds) > col_gust_dir else ""

        if "静穏" in w_spd_raw or "静穏" in w_dir_raw:
            wind_speed = 0.0
            wind_dir = None
        else:
            wind_speed = _clean_val(w_spd_raw)
            wind_dir = w_dir_raw if w_dir_raw and w_dir_raw != "--" else None

        if "静穏" in g_spd_raw or "静穏" in g_dir_raw:
            gust_speed = 0.0
            gust_dir = None
        else:
            gust_speed = _clean_val(g_spd_raw)
            gust_dir = g_dir_raw if g_dir_raw and g_dir_raw != "--" else None

        results.append({
            "time": time_str,  # 保存形式は従来どおり（JST の日時への統一は既存データの移行と合わせて別に行う）
            "wind_speed": wind_speed,
            "wind_dir": wind_dir,
            "gust_speed": gust_speed,
            "gust_dir": gust_dir,
            "temp": temp,
            "precip": precip,
        })

    return results


def fetch_jma_stations(client: PoliteClient, prec_no: int) -> List[Dict[str, Any]]:
    url = f"https://www.data.jma.go.jp/stats/etrn/select/prefecture.php?prec_no={prec_no}&block_no=&year=&month=&day=&view="
    html = client.get_html(url)
    return parse_jma_stations(html, prec_no)


def fetch_jma_10min(client: PoliteClient, station: Dict[str, Any], date_str: str) -> List[Dict[str, Any]]:
    kind = station["kind"]
    prec_no = station["prec_no"]
    block_no = station["block_no"]

    parts = date_str.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date_str format: {date_str}, expected YYYY-MM-DD")
    year, month, day = parts[0], parts[1], parts[2]

    file_name = "10min_s1.php" if kind == "s" else "10min_a1.php"
    url = f"https://www.data.jma.go.jp/stats/etrn/view/{file_name}?prec_no={prec_no}&block_no={block_no}&year={year}&month={month}&day={day}&view=p1"

    html = client.get_html(url)
    return parse_jma_10min(html, kind, date_str)
