"""CSV·Parquet 저장과 재읽기가 데이터의 업무 의미를 유지하는지 확인한다.

작성자: 박다솔
작성목적: 숫자처럼 보이는 우편번호가 계산용 숫자로 변형되는 회귀 문제를 막고,
          세 데이터셋의 이중 저장 및 전체 값 재검증 기능을 확인한다.
작성일: 2026-08-03
"""

from pathlib import Path

from src.data_pipeline.api import CollectedData
from src.data_pipeline.models import CountryResponse, IpApiResponse, OpenMeteoResponse
from src.data_pipeline.storage import normalize, save_and_verify


def test_round_trip_preserves_numeric_looking_zip_code(tmp_path: Path) -> None:
    """우편번호 '20149'가 CSV 저장 후에도 문자열 값으로 보존되어야 한다.

    ``tmp_path``는 테스트가 끝나면 정리되는 임시 폴더이므로 실제 ``output`` 산출물에
    영향을 주지 않는다. 모델 검증을 통과한 자료를 실제 운영 흐름과 똑같이 정규화하고
    CSV·Parquet으로 저장한 뒤 재읽기 검증까지 실행한다.
    """

    collected = CollectedData(
        weather=OpenMeteoResponse.model_validate(
            {
                "latitude": 37.55,
                "longitude": 127.0,
                "timezone": "Asia/Seoul",
                "hourly": {
                    "time": ["2026-08-03T00:00"],
                    "temperature_2m": [25.3],
                    "precipitation_probability": [0],
                },
            }
        ),
        country=CountryResponse.model_validate(
            {
                "alpha3Code": "KOR",
                "name": "Korea (Republic of)",
                "capital": "Seoul",
                "region": "Asia",
                "latlng": [37, 127.5],
                "population": 51_780_579,
            }
        ),
        ip_location=IpApiResponse.model_validate(
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
            }
        ),
    )

    paths = save_and_verify(normalize(collected), tmp_path)

    # CSV 원문에서도 여덟 번째 값인 우편번호가 앞자리 손실 없이 그대로인지 확인한다.
    csv_text = paths["ip_location"][0].read_text(encoding="utf-8")
    assert csv_text.splitlines()[1].split(",")[7] == "20149"
