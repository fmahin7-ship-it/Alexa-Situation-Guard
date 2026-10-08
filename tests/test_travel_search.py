"""travel_search — request rules, providers, and safe defaults. No network: AWS is stubbed."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber
from pydantic import ValidationError

from app.engine import load_situation
from app.travel.aws_location import AmazonLocationProvider, calculate_routes_params
from app.travel.recorded import RecordedProvider
from app.travel.search import TRAVEL_ENV, TravelRequest, TravelSearchError, make_travel_provider, travel_search

RECORDED = Path(__file__).resolve().parent.parent / "data" / "recorded"
CENTRAL = [151.2063, -33.883]
AIRPORT = [151.1664, -33.9361]


def _request(**fields) -> TravelRequest:
    base = dict(origin=CENTRAL, destination=AIRPORT, mode="Transit", depart_at="2026-10-02T15:00:00+10:00")
    base.update(fields)
    return TravelRequest(**base)


def _as_stub_reply(response: dict) -> dict:
    """
    Real CalculateRoutes replies omit some members the service model marks required
    (empty lists, presumably). botocore's Stubber validates replies against the model,
    so fill those in as empty lists. Our parser does not read any of them.
    """
    reply = json.loads(json.dumps(response))
    for route in reply["Routes"]:
        route.setdefault("MajorRoadLabels", [])
        for leg in route["Legs"]:
            details = leg.get("PedestrianLegDetails")
            if details is None:
                continue
            details.setdefault("AfterTravelSteps", [])
            details.setdefault("Notices", [])
            for step in details.get("TravelSteps", []):
                if "TurnStepDetails" in step:
                    step["TurnStepDetails"].setdefault("Intersection", [])
    return reply


def _stubbed_provider():
    """Provider whose client is a botocore Stubber: any unexpected call fails, nothing reaches AWS."""
    client = boto3.client(
        "geo-routes",
        region_name="ap-southeast-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    return AmazonLocationProvider(client=client), Stubber(client)


# --- TravelRequest ----------------------------------------------------------


def test_request_needs_exactly_one_of_depart_or_arrive():
    with pytest.raises(ValidationError, match="exactly one"):
        _request(arrive_by="2026-10-02T16:00:00+10:00")  # both
    with pytest.raises(ValidationError, match="exactly one"):
        _request(depart_at=None)  # neither


def test_request_times_need_an_offset():
    with pytest.raises(ValidationError, match="offset"):
        _request(depart_at="2026-10-02T15:00:00")


def test_request_built_from_situation_locations():
    trip = load_situation("travel_friday")
    places = {loc.id: loc for loc in trip.locations}
    request = TravelRequest.between(
        places["loc_central"], places["loc_airport_t1"], mode="Transit", arrive_by="2026-10-02T16:00:00+10:00"
    )
    assert request.origin == CENTRAL and request.destination == AIRPORT


# --- AWS request shape (no call) --------------------------------------------


def test_aws_params_for_arrive_by_with_alternatives():
    params = calculate_routes_params(_request(depart_at=None, arrive_by="2026-10-02T16:00:00+10:00"))
    assert params == {
        "Origin": CENTRAL,
        "Destination": AIRPORT,
        "TravelMode": "Transit",
        "LegAdditionalFeatures": ["Summary"],
        "ArrivalTime": "2026-10-02T16:00:00+10:00",
        "MaxAlternatives": 2,
    }


def test_aws_params_walk_is_pedestrian_and_zero_alternatives_is_omitted():
    params = calculate_routes_params(_request(mode="Walk", max_alternatives=0))
    assert params["TravelMode"] == "Pedestrian"
    assert "MaxAlternatives" not in params


# --- live provider, stubbed -------------------------------------------------


def test_live_provider_sends_the_exact_request_and_parses_the_reply():
    provider, stub = _stubbed_provider()
    request = _request()
    reply = _as_stub_reply(json.loads((RECORDED / "route_transit_alternatives.json").read_text(encoding="utf-8")))
    stub.add_response("calculate_routes", reply, expected_params=calculate_routes_params(request))

    with stub:
        result = travel_search(request, provider)
    stub.assert_no_pending_responses()

    assert result.live is True and result.provider == "amazon-location"
    assert len(result.options) == 3 and result.rejected == []
    assert result.options[0].id == "amazon-location:transit:depart-2026-10-02T15:00:00+10:00:route1"


def test_live_provider_turns_aws_errors_into_clear_search_errors():
    provider, stub = _stubbed_provider()
    stub.add_client_error("calculate_routes", "AccessDeniedException", "not allowed")
    with stub, pytest.raises(TravelSearchError, match="AccessDeniedException"):
        travel_search(_request(), provider)


def test_missing_aws_profile_is_a_clear_error_not_a_crash():
    provider = AmazonLocationProvider(profile="situation-guard-profile-that-does-not-exist")
    with pytest.raises(TravelSearchError, match="aws configure --profile"):
        travel_search(_request(), provider)


# --- recorded provider ------------------------------------------------------


def test_recorded_provider_replays_a_recorded_question():
    result = travel_search(_request(), RecordedProvider())
    assert result.live is False
    assert [o.id for o in result.options] == [
        "route_transit_alternatives:route1",
        "route_transit_alternatives:route2",
        "route_transit_alternatives:route3",
    ]


def test_recorded_provider_matches_the_same_moment_in_another_offset():
    # 05:00 UTC is 15:00 in Sydney: the same moment, so the same recording.
    result = travel_search(_request(depart_at="2026-10-02T05:00:00+00:00"), RecordedProvider())
    assert len(result.options) == 3


def test_recorded_provider_refuses_a_question_it_never_recorded():
    with pytest.raises(TravelSearchError, match="No recording"):
        travel_search(_request(depart_at="2026-10-02T15:30:00+10:00"), RecordedProvider())
    with pytest.raises(TravelSearchError, match="No recording"):
        travel_search(_request(origin=[144.9631, -37.8136]), RecordedProvider())  # Melbourne


def test_every_indexed_recording_replays():
    provider = RecordedProvider()
    index = json.loads((RECORDED / "index.json").read_text(encoding="utf-8"))
    for entry in index["recordings"]:
        result = travel_search(TravelRequest(**entry["request"]), provider)
        assert result.options and result.rejected == []


# --- choosing a provider ----------------------------------------------------


def test_default_provider_is_recorded_so_nothing_costs_money(monkeypatch):
    monkeypatch.delenv(TRAVEL_ENV, raising=False)
    assert make_travel_provider().live is False


def test_live_provider_only_when_asked(monkeypatch):
    monkeypatch.setenv(TRAVEL_ENV, "live")
    assert isinstance(make_travel_provider(), AmazonLocationProvider)  # created, not called


def test_unknown_provider_setting_is_an_error(monkeypatch):
    monkeypatch.setenv(TRAVEL_ENV, "google")
    with pytest.raises(TravelSearchError, match="'recorded' or 'live'"):
        make_travel_provider()
