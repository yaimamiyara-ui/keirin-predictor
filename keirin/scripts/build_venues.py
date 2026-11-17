import json
import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from keirin.fetch.http import PoliteClient
from keirin.fetch.client import ChariClient
from keirin.fetch.chariloto import fetch_velinfo
from keirin.fetch.jma import parse_jma_prec_list, fetch_jma_stations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 場コード→場名（チャリロト・WINTICKETの場コードと共通）。Claude修正：コードは文字列、場名を定数で持つ
VENUE_NAMES = {
    "11": "函館", "12": "青森", "13": "いわき平", "21": "弥彦", "22": "前橋", "23": "取手", "24": "宇都宮",
    "25": "大宮", "26": "西武園", "27": "京王閣", "28": "立川", "31": "松戸", "32": "千葉", "34": "川崎",
    "35": "平塚", "36": "小田原", "37": "伊東", "38": "静岡", "42": "名古屋", "43": "岐阜", "44": "大垣",
    "45": "豊橋", "46": "富山", "47": "松阪", "48": "四日市", "51": "福井", "53": "奈良", "54": "向日町",
    "55": "和歌山", "56": "岸和田", "61": "玉野", "62": "広島", "63": "防府", "71": "高松", "73": "小松島",
    "74": "高知", "75": "松山", "81": "小倉", "83": "久留米", "84": "武雄", "85": "佐世保", "86": "別府", "87": "熊本",
}
VENUE_CODES = list(VENUE_NAMES)

INDOOR_VENUES = {"22", "32", "81"}  # 前橋, 千葉, 小倉


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def get_address_coordinates(client: PoliteClient, address: str) -> Optional[List[float]]:
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
    try:
        res = client.get_json(url, params={"q": address})
        if isinstance(res, list) and len(res) > 0:
            coords = res[0].get("geometry", {}).get("coordinates", [])
            if len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
                return [lat, lon]
    except Exception as e:
        logger.warning(f"Failed to geocode address '{address}': {e}")
    return None


def collect_all_active_jma_stations(client: PoliteClient) -> List[Dict[str, Any]]:
    logger.info("Fetching JMA prefecture list...")
    try:
        pref_html = client.get_html("https://www.data.jma.go.jp/stats/etrn/select/prefecture00.php")
        prec_nos = parse_jma_prec_list(pref_html)
    except Exception as e:
        logger.warning(f"Could not fetch prefecture list: {e}")
        prec_nos = []

    if not prec_nos:
        prec_nos = list(range(11, 100))

    all_stations = []
    for p_no in prec_nos:
        try:
            stns = fetch_jma_stations(client, p_no)
            for stn in stns:
                if stn.get("has_wind") and stn.get("end_date") is None:
                    all_stations.append(stn)
        except Exception as e:
            logger.warning(f"Failed to fetch JMA stations for prec_no={p_no}: {e}")

    return all_stations


def main() -> None:
    client = PoliteClient(min_interval=3.0)
    chari = ChariClient(min_interval=3.0)

    jma_stations = collect_all_active_jma_stations(client)
    logger.info(f"Collected {len(jma_stations)} active JMA stations with wind data.")

    venues_data = []

    for vel in VENUE_CODES:
        logger.info(f"Processing venue code {vel}...")
        try:
            v_info = fetch_velinfo(chari, vel)
        except Exception as e:
            logger.warning(f"Skipping venue {vel} due to velinfo fetch error: {e}")
            continue

        address = v_info.get("address", "")
        coords = get_address_coordinates(client, address) if address else None

        lat = coords[0] if coords else None
        lon = coords[1] if coords else None

        nearest_jma = None
        min_dist = float("inf")

        if lat is not None and lon is not None and jma_stations:
            for stn in jma_stations:
                dist = haversine_km(lat, lon, stn["lat"], stn["lon"])
                if dist < min_dist:
                    min_dist = dist
                    nearest_jma = {
                        "kind": stn["kind"],
                        "prec_no": stn["prec_no"],
                        "block_no": stn["block_no"],
                        "name": stn["name"],
                        "lat": stn["lat"],
                        "lon": stn["lon"],
                        "distance_km": round(dist, 2),
                    }

        is_indoor = vel in INDOOR_VENUES

        venue_record = {
            "vel_code": vel,
            "name": VENUE_NAMES[vel],
            "address": address,
            "lat": lat,
            "lon": lon,
            "is_indoor": is_indoor,
            "jma": nearest_jma,
            # チャリロトの競輪場データ（周長・直線・傾斜・幅員・決まり手%）をそのまま入れる
            "bank": {k: v for k, v in v_info.items() if k not in ("vel_code", "address")},
        }
        venues_data.append(venue_record)

    out_dir = Path("config")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "venues.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(venues_data, f, ensure_ascii=False, indent=2)

    logger.info(f"Successfully wrote {len(venues_data)} venues to {out_file}")


if __name__ == "__main__":
    main()
