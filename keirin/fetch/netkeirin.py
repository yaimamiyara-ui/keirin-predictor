import re
import unicodedata
import logging
from typing import Any, Dict, List, Optional, Union
from datetime import date, datetime
from bs4 import BeautifulSoup
from keirin.fetch.http import PoliteClient, PoliteError

logger = logging.getLogger(__name__)


def make_race_id(race_date: Union[str, date, datetime], vel_code: Union[int, str], race_num: Union[int, str]) -> str:
    if isinstance(race_date, (date, datetime)):
        d_str = race_date.strftime("%Y%m%d")
    else:
        d_str = str(race_date).replace("-", "").replace("/", "")
    
    vel_str = f"{int(vel_code):02d}"
    r_str = f"{int(race_num):02d}"
    return f"{d_str}{vel_str}{r_str}"


def _clean_text(val: Optional[str]) -> str:
    if val is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(val))
    return normalized.strip()


def _parse_float(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    cleaned = _clean_text(val).replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(val: Optional[str]) -> Optional[int]:
    if not val:
        return None
    cleaned = _clean_text(val)
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_nk_entry(html: str, race_id: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    # 1. Lineup
    lineup = None
    lineup_order = None

    deploy_box = soup.find("div", class_=re.compile(r"DeployBox"))
    if deploy_box:
        in_boxes = deploy_box.find_all("div", class_="DeployInBox")
        lines = []
        current_line = []
        all_cars = []

        for box in in_boxes:
            if box.find("span", class_="WakuSeparat"):
                if current_line:
                    lines.append("-".join(current_line))
                    current_line = []
            else:
                num_elem = box.find("span", class_=re.compile(r"Shaban_Num"))
                if num_elem:
                    c_num = _clean_text(num_elem.get_text())
                    if c_num.isdigit():
                        current_line.append(c_num)
                        all_cars.append(c_num)

        if current_line:
            lines.append("-".join(current_line))

        if lines and len(lines) > 1:
            lineup = "/".join(lines)
            lineup_order = ",".join(all_cars)
        elif all_cars:
            lineup_order = ",".join(all_cars)
            lineup = lineup_order if lines else None
    
    if not lineup_order and not lineup:
        lines = []
        current_line = []
        all_cars = []
        for box in soup.select(".DeployYoso .DeployInBox"):
            if box.select_one(".WakuSeparat"):
                if current_line:
                    lines.append("-".join(current_line))
                    current_line = []
            else:
                num = box.select_one(".Shaban_Num")
                if num and num.text.strip().isdigit():
                    c = num.text.strip()
                    current_line.append(c)
                    all_cars.append(c)
        if current_line:
            lines.append("-".join(current_line))
        if lines:
            lineup = "/".join(lines) if len(lines) > 1 else lines[0]
            lineup_order = ",".join(all_cars)

    # 2. Entries (Detailed Table)
    table = soup.find("table", id="RaceCard_Table_Fixed") or soup.find("table", class_=re.compile(r"RaceCard_Table"))
    if not table:
        raise ValueError("Race table not found in HTML")

    entries = []
    rows = table.find_all("tr", class_="PlayerList")

    for row in rows:
        tds = row.find_all("td")
        if not tds or len(tds) < 5:
            continue

        car_num_str = _clean_text(tds[1].get_text()) if len(tds) > 1 else ""
        car_num = _parse_int(car_num_str)

        honshi_mark = None
        yoso_td = row.find("td", class_=re.compile(r"Yoso"))
        if yoso_td:
            txt = _clean_text(yoso_td.get_text())
            m = re.search(r"[◎○◯▲△×注☆✓]", txt)
            if m:
                honshi_mark = m.group(0)

        name_elem = row.find("a", class_=re.compile(r"Player"))
        player_name = _clean_text(name_elem.get_text()) if name_elem else ""

        score = None
        style = None
        s_cnt = None
        h_cnt = None
        b_cnt = None
        nige = None
        makuri = None
        sashi = None
        mark = None
        c1st = None
        c2nd = None
        c3rd = None
        out_of = None
        win_rate = None
        r2_rate = None
        r3_rate = None
        gear = None
        comment = None

        cells = [td for td in tds if "Player_Select" not in td.get("class", [])]
        idx = 4
        if len(cells) > idx:
            score = _parse_float(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            raw_style = _clean_text(cells[idx].get_text())
            style = re.sub(r"^\d+", "", raw_style)
            idx += 1
        if len(cells) > idx:
            s_cnt = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            h_cnt = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            b_cnt = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            nige = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            makuri = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            sashi = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            mark = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            c1st = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            c2nd = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            c3rd = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            out_of = _parse_int(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            win_rate = _parse_float(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            r2_rate = _parse_float(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            r3_rate = _parse_float(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            gear = _parse_float(cells[idx].get_text())
            idx += 1
        if len(cells) > idx:
            comment = _clean_text(cells[idx].get_text())

        entries.append({
            "car_num": car_num,
            "name": player_name,
            "honshi_mark": honshi_mark,
            "score": score,
            "style": style,
            "s_cnt": s_cnt,
            "h_cnt": h_cnt,
            "b_cnt": b_cnt,
            "nige": nige,
            "makuri": makuri,
            "sashi": sashi,
            "mark": mark,
            "first_place": c1st,
            "second_place": c2nd,
            "third_place": c3rd,
            "out_of_place": out_of,
            "win_rate": win_rate,
            "quinella_rate": r2_rate,
            "trio_rate": r3_rate,
            "gear": gear,
            "comment": comment,
            "pref": None,
            "age": None,
            "period": None,
            "grade": None,
        })

    # 3. Honshi (Opinion and Bets)
    honshi = None
    kenkai_wrap = soup.find("div", class_="YosoDetailKenkai_Wrap") or soup.find("section", class_="YosoDetailKenkai")
    if kenkai_wrap:
        txt_elem = kenkai_wrap.find("div", class_="YosoKenkaiTxt")
        opinion = _clean_text(txt_elem.get_text()) if txt_elem else ""

        bets = []
        odds_wrap = kenkai_wrap.find("div", class_="OddsWrap")
        if odds_wrap:
            for dtr in odds_wrap.find_all("dd"):
                num_div = dtr.find("div", class_="Num")
                if num_div:
                    b_txt = re.sub(r"\s+", " ", num_div.get_text()).strip()  # 改行・空白をまとめる（Claude修正）
                    if b_txt:
                        bets.append(b_txt)
                else:
                    d_txt = re.sub(r"\s+", " ", dtr.get_text()).strip()
                    if d_txt:
                        bets.append(d_txt)

        if opinion or bets:
            honshi = {
                "opinion": opinion if opinion else None,
                "bets": bets if bets else []
            }

    return {
        "race_id": race_id,
        "lineup": lineup,
        "lineup_order": lineup_order,
        "entries": entries,
        "honshi": honshi,
    }


def parse_nk_comments(html: str) -> List[Dict[str, Any]]:
    """選手コメント一覧（table.Player_Comment）。1選手が2行（車番・名前の行、コメントの行）に分かれている（Claude書き直し）。"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="Player_Comment")
    if not table:
        return []
    comments: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for tr in table.find_all("tr", class_="PlayerList"):
        waku = tr.find("td", class_=re.compile(r"^Waku\d"))
        if waku:
            name = tr.select_one(".Player_Name .Txt")
            current = {"car_num": _parse_int(waku.get_text()),
                       "name": _clean_text(name.get_text()) if name else "", "comment": ""}
            comments.append(current)
            continue
        cell = tr.select_one(".Comment_Cell")
        if cell and current is not None:
            current["comment"] = _clean_text(cell.get_text())
    return comments


def fetch_nk_entry(client: PoliteClient, race_id: str) -> Dict[str, Any]:
    url = f"https://keirin.netkeiba.com/race/entry/?race_id={race_id}"
    html = client.get_html(url)
    return parse_nk_entry(html, race_id)


def fetch_nk_comments(client: PoliteClient, race_id: str) -> List[Dict[str, Any]]:
    url = f"https://keirin.netkeiba.com/race/player_comment/?race_id={race_id}"
    html = client.get_html(url)
    return parse_nk_comments(html)
