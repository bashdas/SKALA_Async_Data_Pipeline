"""검증된 응답을 표로 정리하고 CSV·Parquet으로 안전하게 저장하는 모듈.

작성자: SKALA 교육생
작성목적: 서로 구조가 다른 세 API 결과를 이해하기 쉬운 업무용 표로 분리하고,
          두 파일 형식으로 저장한 뒤 다시 읽어 원본과 같은지 확인한다.
작성일: 2026-08-03

이 모듈은 수집 자료를 문서 보관 형태로 정리하는 "자료 관리 담당자"에 해당한다.
단순히 파일을 만드는 데서 끝나지 않고, 방금 저장한 파일을 다시 열어 전체 값이 같은지
확인한다. 따라서 저장 과정에서 날짜나 우편번호 형식이 달라지는 문제를 즉시 발견한다.
"""

from pathlib import Path

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_object_dtype, is_string_dtype

from .api import CollectedData


def _stable_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    """문자열과 결측값을 저장 전 일관된 pandas 자료형으로 정리한다.

    우편번호 ``20149``는 숫자처럼 보이지만 계산 대상이 아닌 식별 정보다. 문자열로
    고정하지 않으면 CSV를 다시 읽을 때 정수로 바뀔 수 있다. 또한 선택 항목의 ``None``을
    pandas의 공통 결측값으로 맞춰 CSV와 Parquet가 같은 의미를 유지하게 한다.
    """

    normalized = frame.convert_dtypes()
    for name in normalized:
        if is_string_dtype(normalized[name]) or is_object_dtype(normalized[name]):
            normalized[name] = normalized[name].astype("string")
    return normalized


def normalize(data: CollectedData) -> dict[str, pd.DataFrame]:
    """세 API 모델을 각각 한 행이 한 의미를 갖는 표로 변환한다.

    날씨는 한 시간이 한 행이므로 3일 예보가 72행이 된다. 국가와 IP 위치는 대상 하나의
    속성을 설명하므로 각각 1행이다. 구조가 다른 데이터를 억지로 한 표에 합치지 않아
    이후 사용자와 분석 도구가 각 표의 의미를 명확하게 이해할 수 있다.

    Args:
        data: API 수집과 Pydantic 검증을 모두 통과한 세 결과의 묶음.

    Returns:
        ``weather``, ``country``, ``ip_location`` 이름과 DataFrame의 매핑.
    """

    weather = data.weather
    hourly = weather.hourly
    # 배열의 같은 위치에 있는 시각·기온·강수확률을 같은 행에 배치한다.
    weather_frame = pd.DataFrame(
        {
            "api": "Open-Meteo",
            "latitude": weather.latitude,
            "longitude": weather.longitude,
            "timezone": weather.timezone,
            "observed_at": hourly.time,
            "temperature_2m_c": hourly.temperature_2m,
            "precipitation_probability_pct": hourly.precipitation_probability,
        }
    )

    # 국가 정보는 대한민국 한 건을 설명하므로 한 행짜리 표로 만든다.
    country = data.country
    country_frame = pd.DataFrame(
        [
            {
                "api": "Countries.dev",
                "alpha3_code": country.alpha3_code,
                "name": country.name,
                "capital": country.capital,
                "region": country.region,
                "subregion": country.subregion,
                "latitude": country.latlng[0],
                "longitude": country.latlng[1],
                "population": country.population,
            }
        ]
    )

    # IP 모델의 객체형 IP 주소는 파일 호환성을 위해 표준 문자열로 저장한다.
    ip = data.ip_location
    ip_frame = pd.DataFrame(
        [
            {
                "api": "ip-api",
                "query": str(ip.query),
                "country": ip.country,
                "country_code": ip.country_code,
                "region_code": ip.region_code,
                "region_name": ip.region_name,
                "city": ip.city,
                "zip_code": ip.zip_code,
                "latitude": ip.latitude,
                "longitude": ip.longitude,
                "timezone": ip.timezone,
                "isp": ip.isp,
            }
        ]
    )
    return {
        "weather": _stable_dtypes(weather_frame),
        "country": _stable_dtypes(country_frame),
        "ip_location": _stable_dtypes(ip_frame),
    }


def _read_csv_like(path: Path, reference: pd.DataFrame) -> pd.DataFrame:
    """원본 표의 날짜·문자열 의미를 유지하면서 CSV를 다시 읽는다.

    CSV 자체에는 자료형 정보가 없으므로 pandas가 값을 보고 타입을 추측한다. 이때 날짜나
    숫자 형태의 우편번호가 잘못 바뀌지 않도록 원본 DataFrame의 스키마를 읽기 옵션으로
    전달한다. Parquet는 자료형을 자체 보관하므로 별도 옵션이 필요하지 않다.
    """

    date_columns = [name for name in reference if is_datetime64_any_dtype(reference[name])]
    string_columns = {
        name: "string"
        for name in reference
        if is_string_dtype(reference[name]) or is_object_dtype(reference[name])
    }
    return pd.read_csv(
        path,
        parse_dates=date_columns or None,
        dtype=string_columns or None,
    )


def _assert_same(reference: pd.DataFrame, loaded: pd.DataFrame, label: str) -> None:
    """저장 전 표와 재읽은 표가 행·열·값 기준으로 같은지 확인한다.

    부동소수점은 파일 변환 시 극히 작은 표현 차이가 생길 수 있어 10억분의 1 수준의
    오차만 허용한다. 그 외 실제 값이나 배열 구조가 다르면 파일을 신뢰할 수 없으므로
    어느 데이터셋에서 문제가 났는지 포함해 실행을 실패시킨다.
    """

    # 행 수 검사는 이해하기 쉬운 오류를 먼저 제공하기 위해 별도로 수행한다.
    if len(reference) != len(loaded):
        raise AssertionError(f"{label}: row count differs ({len(reference)} != {len(loaded)})")
    try:
        pd.testing.assert_frame_equal(
            reference.reset_index(drop=True),
            loaded.reset_index(drop=True),
            check_dtype=False,
            check_exact=False,
            rtol=1e-9,
            atol=1e-9,
        )
    except AssertionError as exc:
        raise AssertionError(f"{label}: reloaded values differ: {exc}") from exc


def save_and_verify(
    datasets: dict[str, pd.DataFrame], output_root: Path
) -> dict[str, tuple[Path, Path]]:
    """모든 표를 CSV와 Parquet으로 저장하고 즉시 재읽기 검증을 수행한다.

    Args:
        datasets: 데이터셋 이름과 정규화된 표의 매핑.
        output_root: ``csv``와 ``parquet`` 하위 폴더를 만들 기준 경로.

    Returns:
        데이터셋별 CSV 경로와 Parquet 경로. main.py가 이 경로를 화면에 표시한다.

    Raises:
        AssertionError: 어느 형식이든 저장 후 행·열·값이 원본과 달라진 경우.
    """

    csv_dir = output_root / "csv"
    parquet_dir = output_root / "parquet"
    csv_dir.mkdir(parents=True, exist_ok=True)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, tuple[Path, Path]] = {}
    for name, frame in datasets.items():
        csv_path = csv_dir / f"{name}.csv"
        parquet_path = parquet_dir / f"{name}.parquet"
        frame.to_csv(csv_path, index=False)
        frame.to_parquet(parquet_path, index=False, engine="pyarrow")

        # 저장 직후 실제 파일을 새로 읽어 같은 논리 데이터가 보존됐음을 증명한다.
        _assert_same(frame, _read_csv_like(csv_path, frame), f"{name} CSV")
        _assert_same(frame, pd.read_parquet(parquet_path), f"{name} Parquet")
        paths[name] = (csv_path, parquet_path)
    return paths
