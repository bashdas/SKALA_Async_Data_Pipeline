"""세 API 데이터 품질 규칙이 정상·비정상 입력을 올바르게 구분하는지 확인한다.

작성자: 박다솔
작성목적: 외부 API 없이 고정 샘플만 사용해 정상 데이터 통과, 범위·타입 위반 차단,
          Open-Meteo 배열 길이 불일치 차단을 반복 가능하게 검증한다.
작성일: 2026-08-03
"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.data_pipeline.models import CountryResponse, IpApiResponse, OpenMeteoResponse


@pytest.fixture
def weather_payload() -> dict:
    """여러 테스트가 공통으로 사용할 정상적인 두 시간 분량 날씨 샘플."""

    return {
        "latitude": 37.55,
        "longitude": 127.0,
        "timezone": "Asia/Seoul",
        "hourly": {
            "time": ["2026-08-03T00:00", "2026-08-03T01:00"],
            "temperature_2m": [25.3, 24.9],
            "precipitation_probability": [0, 10],
        },
    }


def test_valid_payloads_pass(weather_payload: dict) -> None:
    """실제 응답 구조를 반영한 정상 날씨·국가·IP 데이터가 모두 통과해야 한다."""

    weather = OpenMeteoResponse.model_validate(weather_payload)
    country = CountryResponse.model_validate(
        {
            "alpha3Code": "KOR",
            "name": "Korea (Republic of)",
            "capital": "Seoul",
            "region": "Asia",
            "subregion": "Eastern Asia",
            "latlng": [37, 127.5],
            "population": 51_780_579,
        }
    )
    ip_location = IpApiResponse.model_validate(
        {
            "status": "success",
            "query": "8.8.8.8",
            "country": "United States",
            "countryCode": "US",
            "region": "VA",
            "regionName": "Virginia",
            "city": "Ashburn",
            "zip": "20149",
            "lat": 39.03,
            "lon": -77.5,
            "timezone": "America/New_York",
            "isp": "Google LLC",
        }
    )

    assert len(weather.hourly.time) == 2
    assert country.alpha3_code == "KOR"
    assert str(ip_location.query) == "8.8.8.8"


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("latitude",), 91),
        (("hourly", "temperature_2m"), [25.3, 100]),
        (("hourly", "precipitation_probability"), [0, 101]),
    ],
)
def test_invalid_ranges_fail(
    weather_payload: dict, path: tuple[str, ...], invalid_value: object
) -> None:
    """위도·기온·강수확률의 업무 허용 범위를 벗어난 값은 저장 전에 거부한다."""

    # 원본 정상 샘플을 보호하기 위해 복사본의 지정된 필드만 비정상 값으로 바꾼다.
    payload = deepcopy(weather_payload)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = invalid_value

    with pytest.raises(ValidationError):
        OpenMeteoResponse.model_validate(payload)


def test_invalid_type_fails(weather_payload: dict) -> None:
    """기온 위치에 숫자로 변환할 수 없는 문자열이 오면 검증에 실패해야 한다."""

    weather_payload["hourly"]["temperature_2m"] = ["not-a-number", 24.9]
    with pytest.raises(ValidationError):
        OpenMeteoResponse.model_validate(weather_payload)


def test_hourly_array_length_mismatch_fails(weather_payload: dict) -> None:
    """시각별 기온과 강수확률의 개수가 다르면 잘못된 시계열로 판단해야 한다."""

    weather_payload["hourly"]["precipitation_probability"] = [0]
    with pytest.raises(ValidationError, match="array lengths differ"):
        OpenMeteoResponse.model_validate(weather_payload)
