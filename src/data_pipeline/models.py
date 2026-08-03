"""외부 API 응답이 업무에서 사용할 수 있는 데이터인지 확인하는 검증 모델.

작성자: 박다솔
작성목적: 세 API의 실제 응답 중 필요한 필드만 선별하고, 잘못된 위치·온도·확률·IP 등이
          저장 단계로 넘어가지 않도록 Pydantic v2로 데이터 품질 규칙을 적용한다.
작성일: 2026-08-03

이 파일의 모델은 외부 시스템이 보내 준 JSON을 신뢰 가능한 내부 데이터로 바꾸는
"품질 검문소" 역할을 한다. API가 예상과 다른 값을 보내면 여기서 즉시 중단되므로,
이후 보고서나 분석 파일에 잘못된 값이 조용히 섞이는 것을 방지할 수 있다.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, model_validator

# 여러 API에 반복되는 업무 규칙을 공통 타입으로 정의한다. 예를 들어 위도 91도처럼
# 지구상에 존재할 수 없는 값이나 강수확률 101%는 모델 생성 단계에서 거부된다.
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Temperature = Annotated[float, Field(ge=-100, le=70)]
Probability = Annotated[int, Field(ge=0, le=100)]


class ApiModel(BaseModel):
    """모든 API 응답 모델이 공유하는 기본 처리 원칙.

    공급자가 새로운 필드를 추가해도 현재 업무에 필요한 필드만 검증해 사용한다.
    ``populate_by_name``은 외부 필드명과 내부의 읽기 쉬운 이름을 모두 허용한다.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class OpenMeteoHourly(ApiModel):
    """한 시각마다 서로 짝을 이루는 날씨 시계열 데이터.

    ``time``의 첫 번째 시각은 두 수치 배열의 첫 번째 값과 같은 관측 시점을 뜻한다.
    따라서 세 배열의 길이가 다르면 어떤 수치가 어느 시각의 값인지 알 수 없으므로
    아래 검증 함수에서 전체 응답을 실패 처리한다.
    """

    time: list[datetime]
    temperature_2m: list[Temperature]
    precipitation_probability: list[Probability]

    @model_validator(mode="after")
    def arrays_have_equal_length(self) -> "OpenMeteoHourly":
        """시간·기온·강수확률이 빠짐없이 일대일로 대응하는지 확인한다."""

        lengths = {
            "time": len(self.time),
            "temperature_2m": len(self.temperature_2m),
            "precipitation_probability": len(self.precipitation_probability),
        }
        if len(set(lengths.values())) != 1:
            raise ValueError(f"Open-Meteo hourly array lengths differ: {lengths}")
        if not self.time:
            raise ValueError("Open-Meteo hourly arrays must not be empty")
        return self


class OpenMeteoResponse(ApiModel):
    """서울 3일 예보에서 저장에 필요한 위치, 시간대, 시간별 날씨."""

    latitude: Latitude
    longitude: Longitude
    timezone: str = Field(min_length=1)
    hourly: OpenMeteoHourly


class CountryResponse(ApiModel):
    """Countries.dev가 제공한 대한민국 기본 정보.

    API의 camelCase 이름은 ``alias``로 연결하고, 코드 안에서는 이해하기 쉬운
    snake_case 이름을 사용한다. 수도와 세부 지역은 공급자에 따라 없을 수 있어
    선택 항목으로 선언한다.
    """

    alpha3_code: str = Field(alias="alpha3Code", pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1)
    capital: str | None = None
    region: str = Field(min_length=1)
    subregion: str | None = None
    latlng: tuple[Latitude, Longitude]
    population: int = Field(ge=0)


class IpApiResponse(ApiModel):
    """8.8.8.8 IP 주소의 지역 확인 결과.

    ``status``가 success인 응답만 허용하며, IP 형식과 위치 범위도 함께 검증한다.
    도시·우편번호·통신사처럼 API가 제공하지 않을 수 있는 값은 선택 항목이다.
    """

    status: Literal["success"]
    query: IPvAnyAddress
    country: str = Field(min_length=1)
    country_code: str = Field(alias="countryCode", pattern=r"^[A-Z]{2}$")
    region_code: str | None = Field(default=None, alias="region")
    region_name: str | None = Field(default=None, alias="regionName")
    city: str | None = None
    zip_code: str | None = Field(default=None, alias="zip")
    latitude: Latitude = Field(alias="lat")
    longitude: Longitude = Field(alias="lon")
    timezone: str = Field(min_length=1)
    isp: str | None = None
