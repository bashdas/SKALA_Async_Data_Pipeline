"""CSV와 Parquet의 실제 읽기·쓰기 시간 및 파일 크기를 비교하는 모듈.

작성자: 박다솔
작성목적: 특정 형식이 항상 빠르다는 일반론 대신, 이번에 수집한 동일 데이터와 현재
          실행 환경에서 반복 측정한 근거를 제공한다.
작성일: 2026-08-03

측정 대상이 작으면 운영체제 캐시나 순간 부하에 따라 결과가 크게 달라질 수 있다.
그래서 한 번의 기록을 결론으로 사용하지 않고 전체 데이터셋 작업을 기본 20회 반복한
평균을 사용한다. 두 형식 모두 같은 세 데이터셋과 같은 반복 횟수를 사용한다.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_object_dtype, is_string_dtype


@dataclass(frozen=True)
class BenchmarkResult:
    """수치 표와 사람이 바로 읽을 수 있는 요약 문장을 함께 전달하는 결과 객체."""

    table: pd.DataFrame
    analysis: str


def _csv_read_options(frame: pd.DataFrame) -> dict[str, object]:
    """CSV 읽기 성능 측정에도 실제 재읽기와 동일한 자료형 조건을 적용한다.

    날짜와 문자열을 올바른 업무 타입으로 복원하는 시간까지 CSV 읽기 시간에 포함한다.
    Parquet는 파일 내부 스키마를 자동 복원하므로 별도 옵션이 필요하지 않다.
    """

    date_columns = [name for name in frame if is_datetime64_any_dtype(frame[name])]
    string_columns = {
        name: "string"
        for name in frame
        if is_string_dtype(frame[name]) or is_object_dtype(frame[name])
    }
    options: dict[str, object] = {}
    if date_columns:
        options["parse_dates"] = date_columns
    if string_columns:
        options["dtype"] = string_columns
    return options


def benchmark_formats(
    datasets: dict[str, pd.DataFrame], output_root: Path, repeats: int = 20
) -> BenchmarkResult:
    """동일 데이터의 CSV·Parquet 전체 쓰기와 읽기를 반복 측정한다.

    Args:
        datasets: 비교에 사용할 정규화된 세 DataFrame.
        output_root: 최종 파일 크기와 성능 결과 파일을 확인할 출력 폴더.
        repeats: 평균 계산에 사용할 반복 횟수. 우연한 1회 결과를 피하도록 2 이상이어야 한다.

    Returns:
        형식별 평균 시간·크기 표와 실측 우세 형식을 설명하는 문장.

    측정용 파일은 임시 폴더에서 매회 같은 이름으로 덮어쓰며, 함수가 끝나면 자동으로
    삭제된다. 최종 크기는 사용자가 실제로 받는 ``output`` 파일을 기준으로 계산한다.
    """

    if repeats < 2:
        raise ValueError("repeats must be at least 2")

    # 초 단위 원시 측정값을 모두 보관한 뒤 마지막에 평균 밀리초로 변환한다.
    timings = {"CSV": {"write": [], "read": []}, "Parquet": {"write": [], "read": []}}
    with tempfile.TemporaryDirectory(prefix="io_benchmark_", dir=output_root) as temp_name:
        temp_dir = Path(temp_name)
        for _ in range(repeats):
            # CSV 쓰기: 세 DataFrame을 모두 기록하는 데 걸린 총시간을 한 회로 본다.
            started = perf_counter()
            for name, frame in datasets.items():
                frame.to_csv(temp_dir / f"{name}.csv", index=False)
            timings["CSV"]["write"].append(perf_counter() - started)

            # CSV 읽기: 자료형 복원 옵션을 포함해 세 파일을 모두 읽는다.
            started = perf_counter()
            for name, frame in datasets.items():
                pd.read_csv(temp_dir / f"{name}.csv", **_csv_read_options(frame))
            timings["CSV"]["read"].append(perf_counter() - started)

            # Parquet도 CSV와 똑같은 세 표를 같은 순서로 쓰고 읽는다.
            started = perf_counter()
            for name, frame in datasets.items():
                frame.to_parquet(temp_dir / f"{name}.parquet", index=False, engine="pyarrow")
            timings["Parquet"]["write"].append(perf_counter() - started)

            started = perf_counter()
            for name in datasets:
                pd.read_parquet(temp_dir / f"{name}.parquet", engine="pyarrow")
            timings["Parquet"]["read"].append(perf_counter() - started)

    # 사용자에게 제공된 최종 세 파일의 크기 합계와 반복 평균을 결과 표로 만든다.
    rows = []
    for format_name, extension in (("CSV", "csv"), ("Parquet", "parquet")):
        size = sum(
            (output_root / extension / f"{name}.{extension}").stat().st_size
            for name in datasets
        )
        rows.append(
            {
                "format": format_name,
                "repeats": repeats,
                "avg_write_ms": sum(timings[format_name]["write"]) / repeats * 1000,
                "avg_read_ms": sum(timings[format_name]["read"]) / repeats * 1000,
                "total_size_bytes": size,
            }
        )

    # 다른 도구에서도 결과를 분석할 수 있도록 수치 원본을 별도 CSV로 남긴다.
    table = pd.DataFrame(rows)
    table.to_csv(output_root / "performance_results.csv", index=False)
    # 각 지표의 최솟값을 실제 우세 형식으로 선정한다. 작은 데이터라는 한계도 함께 알린다.
    indexed = table.set_index("format")
    write_winner = indexed["avg_write_ms"].idxmin()
    read_winner = indexed["avg_read_ms"].idxmin()
    size_winner = indexed["total_size_bytes"].idxmin()
    analysis = (
        f"실측 기준 쓰기는 {write_winner}, 읽기는 {read_winner}, "
        f"전체 파일 크기는 {size_winner} 형식이 우세합니다. "
        "데이터가 작아 절대 시간 차이는 실행 환경과 캐시의 영향을 받을 수 있습니다."
    )
    return BenchmarkResult(table=table, analysis=analysis)
