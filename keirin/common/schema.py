from typing import Any, Optional
from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MeetingRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    vel_name: Optional[str] = None
    open_grade_code: Optional[str] = None
    race_name: Optional[str] = None
    sub_name: Optional[str] = None
    spent_days_string: Optional[str] = None
    night_race_type: Optional[str] = None
    is_girls: Optional[bool] = None
    rehold_string: Optional[str] = None


class CalendarRow(BaseSchema):
    venue_name: Optional[str] = None
    day: Optional[int] = None
    grade: Optional[str] = None
    flags: Optional[list[str]] = None


class RaceRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    race_name: Optional[str] = None
    sub_name: Optional[str] = None
    grade: Optional[str] = None
    spent_days_string: Optional[str] = None
    night_race_type: Optional[str] = None
    is_girls: Optional[bool] = None
    sale_to_at: Optional[str] = None
    race_start_at: Optional[str] = None
    distance: Optional[int] = None
    class_name: Optional[str] = None
    event_name: Optional[str] = None
    entry_row: Optional[int] = None
    bet_niwakufuku_sale_status: Optional[str] = None
    bet_niwakutan_sale_status: Optional[str] = None
    bet_nirenfuku_sale_status: Optional[str] = None
    bet_nirentan_sale_status: Optional[str] = None
    bet_wide_sale_status: Optional[str] = None
    bet_sanrenfuku_sale_status: Optional[str] = None
    bet_sanrentan_sale_status: Optional[str] = None
    bet_niwakufuku_result_status: Optional[str] = None
    bet_niwakutan_result_status: Optional[str] = None
    bet_nirenfuku_result_status: Optional[str] = None
    bet_nirentan_result_status: Optional[str] = None
    bet_wide_result_status: Optional[str] = None
    bet_sanrenfuku_result_status: Optional[str] = None
    bet_sanrentan_result_status: Optional[str] = None


class EntryRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    entry_num: Optional[int] = None
    waku_num: Optional[int] = None
    player_id: Optional[int] = None
    name: Optional[str] = None
    age: Optional[int] = None
    class_name: Optional[str] = None
    group_name: Optional[str] = None
    pref_name: Optional[str] = None
    graduate_period: Optional[int] = None
    gear_ratio: Optional[float] = None
    gear_ratio_before: Optional[float] = None
    average_point: Optional[float] = None
    average_win_raw: Optional[Any] = None
    average_rentai_raw: Optional[Any] = None
    nige_count: Optional[int] = None
    sashi_count: Optional[int] = None
    makuri_count: Optional[int] = None
    mark_count: Optional[int] = None
    standing_count: Optional[int] = None
    back_count: Optional[int] = None
    leg_quality: Optional[str] = None
    first_count: Optional[int] = None
    second_count: Optional[int] = None
    third_count: Optional[int] = None
    out_rank_count: Optional[int] = None
    win_count: Optional[int] = None
    renshou_count: Optional[int] = None
    prize: Optional[int] = None
    rank: Optional[int] = None
    last3d: Optional[int] = None
    comment: Optional[str] = None
    history: Optional[str] = None


class PredictionRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    marks: Optional[str] = None
    sequences: Optional[str] = None
    comment: Optional[str] = None


class OddsRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    bet_type: Optional[str] = None
    combo: Optional[str] = None
    odds_min: Optional[float] = None
    odds_max: Optional[float] = None
    is_valid: Optional[bool] = None
    on_final: Optional[bool] = None
    snapshot: Optional[str] = None


class LineupRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    lineup: Optional[str] = None
    line_count: Optional[int] = None
    lines: Optional[list[list[int]]] = None


class ResultRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    rank: Optional[int] = None
    status: Optional[str] = None
    car_num: Optional[int] = None
    name: Optional[str] = None
    age: Optional[int] = None
    pref: Optional[str] = None
    period: Optional[int] = None
    class_group: Optional[str] = None
    margin: Optional[str] = None
    last_lap: Optional[float] = None
    factor: Optional[str] = None
    sb: Optional[str] = None
    note: Optional[str] = None


class PayoutRow(BaseSchema):
    open_day: Optional[str] = None
    vel_code: Optional[str] = None
    race_num: Optional[int] = None
    bet_type: Optional[str] = None
    combo: Optional[str] = None
    payout_yen: Optional[int] = None


class VenueInfoRow(BaseSchema):
    vel_code: Optional[str] = None
    bank_length_m: Optional[int] = None
    straight_m: Optional[float] = None
    center_incline: Optional[str] = None
    straight_incline: Optional[str] = None
    home_width_m: Optional[float] = None
    back_width_m: Optional[float] = None
    center_width_m: Optional[float] = None
    strike_first_nige: Optional[int] = None
    strike_first_makuri: Optional[int] = None
    strike_first_sashi: Optional[int] = None
    strike_second_nige: Optional[int] = None
    strike_second_makuri: Optional[int] = None
    strike_second_sashi: Optional[int] = None
    strike_second_mark: Optional[int] = None
    updated_at: Optional[str] = None
    address: Optional[str] = None


class AthleteProfileRow(BaseSchema):
    player_id: Optional[int] = None
    registration_num: Optional[str] = None
    birth_date: Optional[str] = None
    period: Optional[int] = None
    class_group: Optional[str] = None
    height_cm: Optional[int] = None
    weight_kg: Optional[int] = None
    home_bank: Optional[str] = None
    pref: Optional[str] = None
