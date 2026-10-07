"""TravelOption parser — uses only the recorded AWS responses, never live AWS."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.travel.aws_location import CAR_TRAFFIC_ASSUMPTION, parse_calculate_routes
from app.travel.option import describe_option, merge_options

RECORDED = Path(__file__).resolve().parent.parent / "data" / "recorded"


def _load(name: str) -> dict:
    return json.loads((RECORDED / f"{name}.json").read_text(encoding="utf-8"))


def _parse(name: str):
    return parse_calculate_routes(_load(name), name)


def _clock(value: str) -> str:
    return value[11:16]


# --- one search, several journeys ------------------------------------------


def test_alternatives_gives_three_separate_options():
    result = _parse("route_transit_alternatives")
    assert result.rejected == []
    assert [o.id for o in result.options] == [
        "route_transit_alternatives:route1",
        "route_transit_alternatives:route2",
        "route_transit_alternatives:route3",
    ]


def test_journey_window_is_first_leg_to_last_leg_not_the_train():
    options = _parse("route_transit_alternatives").options
    assert [(_clock(o.depart_at), _clock(o.arrive_at)) for o in options] == [
        ("15:01", "15:23"),
        ("15:10", "15:32"),
        ("15:02", "15:29"),
    ]


def test_door_to_door_includes_waits_but_aws_duration_does_not():
    route1, _, route3 = _parse("route_transit_alternatives").options
    assert route1.door_to_door_seconds == 22 * 60
    assert route1.moving_seconds == 1320
    # Route 3 waits 15:20-15:24 at Wolli Creek: 27 min door to door, AWS says 23.
    assert route3.door_to_door_seconds == 27 * 60
    assert route3.moving_seconds == 1380


def test_route1_legs_keep_aws_detail_without_inventing_names():
    route1 = _parse("route_transit_alternatives").options[0]
    assert route1.mode == "Transit"
    assert route1.transfers == 0
    assert [leg.kind for leg in route1.legs] == ["walk", "transit", "walk"]

    walk_in, train, walk_out = route1.legs
    assert walk_in.from_name is None  # AWS gave only coordinates
    assert walk_in.to_name == "Central Station"
    assert walk_out.to_name is None
    assert walk_in.from_position == [151.2063, -33.883]

    assert (train.line, train.headsign, train.agency) == ("T8", "Macarthur via Airport", "Sydney Trains")
    assert train.aws_mode == "RegionalTrain"
    assert (_clock(train.depart_at), _clock(train.arrive_at)) == ("15:08", "15:20")
    assert train.duration_s == 720
    assert train.distance_m == 8036


def test_route3_is_two_trains_with_a_change():
    route3 = _parse("route_transit_alternatives").options[2]
    trains = [leg for leg in route3.legs if leg.kind == "transit"]
    assert [t.line for t in trains] == ["T4", "T8"]
    assert trains[0].to_name == "Wolli Creek Station"
    assert route3.transfers == 1


# --- other recorded searches -----------------------------------------------


def test_car_uses_expected_traffic_and_keeps_best_case_separately():
    (car,) = _parse("route_car_depart_1500").options
    assert car.mode == "Car"
    assert car.depart_at == "2026-10-02T15:00:00+10:00"
    assert car.arrive_at == "2026-10-02T15:21:27+10:00"
    assert car.door_to_door_seconds == 1287
    assert car.best_case_seconds == 1006
    assert car.distance_m == 10545
    assert car.assumptions == [CAR_TRAFFIC_ASSUMPTION]
    (drive,) = car.legs
    assert drive.kind == "drive"
    assert drive.from_name is None and drive.to_name is None


def test_arrive_by_gives_latest_trip_that_still_makes_it():
    (option,) = _parse("route_transit_arrive_by_1600").options
    assert (_clock(option.depart_at), _clock(option.arrive_at)) == ("15:31", "15:53")


def test_next_departures_are_ignored_not_turned_into_options():
    result = _parse("route_transit_next_departures")
    assert len(result.options) == 1
    assert (_clock(result.options[0].depart_at), _clock(result.options[0].arrive_at)) == ("15:01", "15:23")


def test_times_are_copied_exactly_and_match_sydney_offset():
    sydney = ZoneInfo("Australia/Sydney")
    for name in ("route_transit_alternatives", "route_car_depart_1500", "route_transit_arrive_by_1600"):
        for option in _parse(name).options:
            for value in [option.depart_at, option.arrive_at] + [
                t for leg in option.legs for t in (leg.depart_at, leg.arrive_at)
            ]:
                dt = datetime.fromisoformat(value)
                assert dt.utcoffset() == dt.astimezone(sydney).utcoffset()


# --- merging searches ------------------------------------------------------


def test_merge_drops_repeated_journeys_and_labels_by_arrival():
    alternatives = _parse("route_transit_alternatives").options
    depart_1500 = _parse("route_transit_depart_1500").options  # same journey as route 1
    car = _parse("route_car_depart_1500").options

    merged = merge_options(alternatives, depart_1500, car)
    assert [o.label for o in merged] == ["A", "B", "C", "D"]
    assert [o.id for o in merged] == [
        "route_car_depart_1500:route1",        # 15:21
        "route_transit_alternatives:route1",   # 15:23
        "route_transit_alternatives:route3",   # 15:29
        "route_transit_alternatives:route2",   # 15:32
    ]


def test_merge_does_not_change_the_input_options():
    alternatives = _parse("route_transit_alternatives").options
    merge_options(alternatives)
    assert all(o.label is None for o in alternatives)


def test_describe_option_is_the_short_line_the_agent_reads():
    merged = merge_options(_parse("route_transit_alternatives").options, _parse("route_car_depart_1500").options)
    assert [describe_option(o) for o in merged] == [
        "A | Car | leave 15:00 -> arrive 15:21 | 21 min | 10.5 km",
        "B | Transit | leave 15:01 -> arrive 15:23 | 22 min | T8",
        "C | Transit | leave 15:02 -> arrive 15:29 | 27 min | T4, change at Wolli Creek Station to T8",
        "D | Transit | leave 15:10 -> arrive 15:32 | 22 min | T8",
    ]


# --- broken responses are rejected with reasons ----------------------------


def _broken_alternatives():
    return copy.deepcopy(_load("route_transit_alternatives"))


def test_route_without_legs_is_rejected_and_others_still_parse():
    response = _broken_alternatives()
    response["Routes"][1]["Legs"] = []
    result = parse_calculate_routes(response, "broken")
    assert [o.id for o in result.options] == ["broken:route1", "broken:route3"]
    assert result.rejected[0].route_index == 1
    assert result.rejected[0].reasons == ["route has no legs"]


def test_time_without_offset_is_rejected():
    response = _broken_alternatives()
    response["Routes"][0]["Legs"][1]["TransitLegDetails"]["Departure"]["Time"] = "2026-10-02T15:08:00"
    result = parse_calculate_routes(response, "broken")
    assert result.rejected[0].route_index == 0
    assert "no offset" in result.rejected[0].reasons[0]


def test_legs_out_of_order_are_rejected():
    response = _broken_alternatives()
    # Train leaves before the walk to the station ends.
    leg = response["Routes"][0]["Legs"][1]["TransitLegDetails"]
    leg["Departure"]["Time"] = "2026-10-02T15:05:00+10:00"
    result = parse_calculate_routes(response, "broken")
    assert result.rejected[0].route_index == 0
    assert "before leg 0 arrives" in result.rejected[0].reasons[0]


def test_unknown_leg_type_is_rejected():
    response = _broken_alternatives()
    response["Routes"][0]["Legs"][0]["Type"] = "Ferry"
    result = parse_calculate_routes(response, "broken")
    assert result.rejected[0].reasons == ["leg 0: unknown leg type 'Ferry'"]


def test_response_without_routes_raises():
    with pytest.raises(ValueError):
        parse_calculate_routes({"Notices": []}, "not_routes")
