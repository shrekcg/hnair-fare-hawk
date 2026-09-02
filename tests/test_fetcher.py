import json

import backend.fetcher as fetcher


def _itinerary_response():
    return {
        "success": True,
        "data": {
            "originDestinations": [
                {
                    "airItineraries": [
                        {
                            "id": "itinerary-1",
                            "minLowPrice": 199,
                            "taxPrice": 120,
                            "flightSegments": [
                                {
                                    "marketingAirlineCode": "HU",
                                    "flightNumber": "7204",
                                }
                            ],
                            "airItineraryPrices": [
                                {
                                    "id": "price-1",
                                    "priceKey": "price-key",
                                    "supplier": "HU",
                                    "inventoryStatus": "3",
                                    "travelerPrices": [{"baseFare": "199"}],
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    }


def test_parse_curl_supports_chrome_url_and_cookie_flags():
    command = """PASTE_MARKER
curl --url 'https://example.test/search?token=masked&hnairSign=signature' \\
  -H 'AppVer: 10.17.2' \\
  -b 'session=masked' \\
  --data-raw '{"data": {"specialZone": "ffl"}}'
"""

    parsed = fetcher.parse_curl_command(command)

    assert parsed["url"].startswith("https://example.test/search")
    assert parsed["headers"]["appver"] == "10.17.2"
    assert parsed["headers"]["cookie"] == "session=masked"
    assert json.loads(parsed["data"])["data"]["specialZone"] == "ffl"


def test_extracts_nested_itineraries_price_and_full_flight_number():
    itineraries = fetcher._extract_itineraries(_itinerary_response())

    assert len(itineraries) == 1
    assert fetcher._extract_itinerary_price(itineraries[0]) == 199
    assert fetcher._format_flight_code(itineraries[0]["flightSegments"][0]) == "HU7204"


def test_captured_plus_profile_keeps_sign_and_updates_route(tmp_path, monkeypatch):
    command = """curl --url 'https://example.test/ffl/airLowFareSearch?token=masked&hnairSign=valid-sign' \\
  -H 'appver: 10.17.2' \\
  -H 'content-type: application/json' \\
  -b 'session=masked' \\
  --data-raw '{"common": {"stime": 123}, "data": {"originDestinations": [{"origin": "AAA", "destination": "BBB", "departureDate": "2026-01-01"}], "specialZone": "ffl"}}'
"""
    (tmp_path / "config.json").write_text(
        json.dumps({"plus_curl": command}), encoding="utf-8"
    )
    monkeypatch.setattr(fetcher, "BASE_DIR", tmp_path)

    profile = fetcher._build_request_profile("SHE", "CAN", "2026-09-16", "plus")
    route = profile["payload"]["data"]["originDestinations"][0]

    assert profile["preserve_captured_sign"] is True
    assert profile["query"]["hnairSign"] == "valid-sign"
    assert profile["headers"]["cookie"] == "session=masked"
    assert route == {
        "origin": "SHE",
        "destination": "CAN",
        "departureDate": "2026-09-16",
    }


def test_real_fetch_price_parses_current_response(monkeypatch):
    profile = {
        "url": "https://example.test/ffl/airLowFareSearch",
        "query": {"token": "masked", "hnairSign": "valid-sign"},
        "headers": {"cookie": "session=masked"},
        "payload": {"data": {}},
        "preserve_captured_sign": True,
    }

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return _itinerary_response()

    monkeypatch.setattr(fetcher, "_build_request_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr(fetcher.requests, "post", lambda *args, **kwargs: Response())

    assert fetcher.real_fetch_price("SHE", "CAN", "2026-09-16", "plus") == [
        {
            "flight": "HU7204",
            "price": 199,
        }
    ]


def _mk_profile(url="https://example.test/ffl/airLowFareSearch"):
    return {
        "url": url,
        "query": {"token": "masked", "hnairSign": "valid-sign"},
        "headers": {"cookie": "session=masked"},
        "payload": {"data": {}},
        "preserve_captured_sign": True,
    }


def _mk_response(status_code=200, data=None):
    class Response:
        pass

    resp = Response()
    resp.status_code = status_code
    resp._data = data if data is not None else {"success": True, "data": {"originDestinations": []}}

    @staticmethod
    def json():
        return resp._data

    resp.json = json
    return resp


def test_fetch_price_status_ok_and_empty(monkeypatch):
    monkeypatch.setattr(fetcher, "_build_request_profile", lambda *a, **k: _mk_profile())
    monkeypatch.setattr(fetcher, "_load_proxy", lambda: "")

    monkeypatch.setattr(
        fetcher.requests,
        "post",
        lambda *a, **k: _mk_response(200, _itinerary_response()),
    )
    status, fares = fetcher.fetch_price_status("SHE", "CAN", "2026-09-16", "plus")
    assert status == "ok"
    assert fares[0]["flight"] == "HU7204"

    monkeypatch.setattr(
        fetcher.requests,
        "post",
        lambda *a, **k: _mk_response(200, {"success": True, "data": {"originDestinations": []}}),
    )
    status, fares = fetcher.fetch_price_status("SHE", "CAN", "2026-09-16", "plus")
    assert status == "empty"
    assert fares == []


def test_fetch_price_status_network_and_parse(monkeypatch):
    monkeypatch.setattr(fetcher, "_build_request_profile", lambda *a, **k: _mk_profile())
    monkeypatch.setattr(fetcher, "_load_proxy", lambda: "")

    monkeypatch.setattr(fetcher.requests, "post", lambda *a, **k: _mk_response(429, {}))
    assert fetcher.fetch_price_status("SHE", "CAN", "2026-09-16", "plus")[0] == "network"

    monkeypatch.setattr(fetcher.requests, "post", lambda *a, **k: _mk_response(200, [1, 2, 3]))
    assert fetcher.fetch_price_status("SHE", "CAN", "2026-09-16", "plus")[0] == "parse"


def test_fetch_price_status_auth_raises_token_expired(monkeypatch):
    monkeypatch.setattr(fetcher, "_build_request_profile", lambda *a, **k: _mk_profile())
    monkeypatch.setattr(fetcher, "_load_proxy", lambda: "")

    monkeypatch.setattr(
        fetcher.requests,
        "post",
        lambda *a, **k: _mk_response(200, {"success": False, "errorCode": "E00001", "errorMessage": "验签错误"}),
    )
    try:
        fetcher.fetch_price_status("SHE", "CAN", "2026-09-16", "plus")
        raise AssertionError("should have raised")
    except fetcher.TokenExpiredError:
        pass
