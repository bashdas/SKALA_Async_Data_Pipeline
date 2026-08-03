"""세 외부 API를 동시에 호출하고 응답을 검증하는 수집 모듈.

작성자: 박다솔
작성목적: 날씨·국가·IP 위치 정보를 순차적으로 기다리지 않고 동시에 요청해 전체 대기
          시간을 줄이고, HTTP 오류와 데이터 품질 오류를 API 이름과 함께 전달한다.
작성일: 2026-08-03

비개발자 관점에서 이 모듈은 세 기관에 자료를 동시에 요청하는 "수집 담당자"다.
각 기관의 회신이 정상인지 확인하고, models.py의 품질 검사를 통과한 자료만 다음 단계로
전달한다. 어느 기관에서 문제가 발생했는지도 오류 메시지에 분명히 표시한다.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .models import CountryResponse, IpApiResponse, OpenMeteoResponse

# 실제 수집 대상이다. Open-Meteo 주소에는 서울 좌표, 3일 기간, 기온·강수확률,
# 서울 시간대가 명시되어 있어 실행할 때마다 같은 조건의 최신 예보를 요청한다.
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=37.5665&longitude=126.9780"
    "&hourly=temperature_2m,precipitation_probability"
    "&forecast_days=3&timezone=Asia/Seoul"
)
COUNTRIES_URL = "https://countries.dev/alpha/KOR"
IP_API_URL = "http://ip-api.com/json/8.8.8.8"

ModelT = TypeVar("ModelT", bound=BaseModel)


class ApiCollectionError(RuntimeError):
    """API 연결, HTTP 상태 또는 JSON 형식에 문제가 있을 때 사용하는 예외."""


class ApiValidationError(RuntimeError):
    """연결은 성공했지만 응답 내용이 업무 품질 규칙에 맞지 않을 때 사용하는 예외."""


@dataclass(frozen=True)
class CollectedData:
    """세 API에서 검증을 마친 결과를 한 묶음으로 전달하는 읽기 전용 컨테이너.

    ``frozen=True``이므로 수집 이후 값이 실수로 바뀌지 않는다. 이 객체는 저장 모듈로
    전달되는 공식 수집 결과이며, 원시 JSON 대신 타입이 확인된 모델을 보관한다.
    """

    weather: OpenMeteoResponse
    country: CountryResponse
    ip_location: IpApiResponse


async def _request_json(
    client: httpx.AsyncClient, api_name: str, url: str
) -> dict[str, Any]:
    """지정된 API를 호출해 정상적인 JSON 객체를 반환한다.

    Args:
        client: 여러 요청이 공동 사용하여 연결 비용을 줄이는 비동기 HTTP 클라이언트.
        api_name: 장애 메시지에 표시할 사람이 읽기 쉬운 서비스 이름.
        url: 호출할 API 주소.

    Returns:
        공급자가 보내 준 JSON 객체. 데이터 필드 검증은 ``_validate``가 담당한다.

    Raises:
        ApiCollectionError: 비정상 HTTP 상태, 연결 실패, JSON 해석 실패가 발생한 경우.
    """

    try:
        # await 중에는 다른 API 요청이 계속 진행될 수 있어 네트워크 대기 시간을 겹친다.
        response = await client.get(url)
        # 4xx·5xx 상태를 성공 데이터로 오인하지 않도록 즉시 예외로 바꾼다.
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ApiCollectionError(
            f"{api_name} HTTP error: status={exc.response.status_code}, url={exc.request.url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ApiCollectionError(f"{api_name} request failed: {exc}") from exc
    except ValueError as exc:
        raise ApiCollectionError(f"{api_name} returned invalid JSON: {exc}") from exc

    # 이 프로젝트의 세 API는 최상위가 JSON 객체라는 실제 응답 계약을 사용한다.
    # 배열이나 단순 문자열이 오면 공급자 계약이 달라진 것이므로 저장하지 않는다.
    if not isinstance(payload, dict):
        raise ApiCollectionError(
            f"{api_name} returned {type(payload).__name__}, expected a JSON object"
        )
    return payload


def _validate(api_name: str, model: type[ModelT], payload: dict[str, Any]) -> ModelT:
    """원시 응답을 API별 Pydantic 모델로 변환하고 품질 오류에 출처를 붙인다.

    Pydantic의 필드별 오류를 그대로 세부 내용에 포함하므로 운영 담당자는 어느 API의
    어느 값이 범위를 벗어났거나 누락됐는지 확인할 수 있다.
    """

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        details = exc.errors(include_url=False)
        raise ApiValidationError(f"{api_name} payload validation failed: {details}") from exc


async def fetch_open_meteo(client: httpx.AsyncClient) -> OpenMeteoResponse:
    """서울의 3일 시간별 기온과 강수확률을 수집·검증한다."""

    payload = await _request_json(client, "Open-Meteo", OPEN_METEO_URL)
    return _validate("Open-Meteo", OpenMeteoResponse, payload)


async def fetch_country(client: httpx.AsyncClient) -> CountryResponse:
    """대한민국의 국가 코드, 수도, 위치, 인구 정보를 수집·검증한다."""

    payload = await _request_json(client, "Countries.dev", COUNTRIES_URL)
    return _validate("Countries.dev", CountryResponse, payload)


async def fetch_ip_location(client: httpx.AsyncClient) -> IpApiResponse:
    """공개 DNS 주소 8.8.8.8의 IP 기반 지역 정보를 수집·검증한다."""

    payload = await _request_json(client, "ip-api", IP_API_URL)
    return _validate("ip-api", IpApiResponse, payload)


async def collect_all() -> CollectedData:
    """하나의 클라이언트로 세 API를 동시에 실행하고 결과를 한 묶음으로 반환한다.

    전체 응답 제한은 15초, 연결 단계 제한은 5초다. 무한정 기다리는 상황을 방지하면서
    일반적인 인터넷 지연은 허용한다. ``asyncio.gather``는 세 업무를 한꺼번에 시작하고
    모두 완료될 때까지 기다리므로 순차 호출보다 전체 수집 시간이 줄어든다.
    """

    timeout = httpx.Timeout(15.0, connect=5.0)
    # User-Agent는 API 운영자가 요청 출처와 목적을 식별할 수 있도록 한다.
    headers = {"User-Agent": "SKALA-async-data-pipeline/1.0"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        # 세 함수에 동일한 client를 전달해 연결 풀을 재사용하고 자원 정리를 한곳에서 한다.
        weather, country, ip_location = await asyncio.gather(
            fetch_open_meteo(client),
            fetch_country(client),
            fetch_ip_location(client),
        )
    return CollectedData(weather=weather, country=country, ip_location=ip_location)
