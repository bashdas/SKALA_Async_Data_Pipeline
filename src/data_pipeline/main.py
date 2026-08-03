"""한 번의 명령으로 데이터 파이프라인 전체 업무를 순서대로 실행하는 진입점.

작성자: 박다솔
작성목적: 사용자가 여러 스크립트를 따로 실행하지 않아도 API 수집, 품질 검증, 표 변환,
          이중 형식 저장, 재읽기 확인, 성능 비교 및 실행 로그 기록을 완료하게 한다.
작성일: 2026-08-03

화면의 ``[1/5]``부터 ``[5/5]``까지가 현재 진행 단계다. 같은 내용은
``report/run_output.txt``에도 자동 저장되어 실행 증빙과 보고서 작성에 활용할 수 있다.
"""

import asyncio
import sys
from contextlib import redirect_stdout
from pathlib import Path
from time import perf_counter
from typing import TextIO

from .api import collect_all
from .benchmark import benchmark_formats
from .storage import normalize, save_and_verify

# 실행 위치가 달라도 항상 프로젝트 폴더를 기준으로 파일을 찾도록 현재 파일의 절대
# 경로에서 루트를 계산한다. 따라서 README의 명령을 프로젝트 루트에서 실행하면 된다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = PROJECT_ROOT / "output"
REPORT_ROOT = PROJECT_ROOT / "report"


class Tee:
    """한 번의 출력 내용을 터미널과 로그 파일 양쪽에 동시에 전달한다.

    수도관의 T자 연결처럼 하나의 텍스트 흐름을 여러 출력 대상으로 나누기 때문에
    ``Tee``라는 이름을 사용한다. 사용자는 진행 상황을 화면에서 보고, 같은 원문은
    실행 증빙 파일로 보관할 수 있다.
    """

    def __init__(self, *streams: TextIO) -> None:
        """출력을 함께 받을 터미널·파일 등의 대상을 등록한다."""

        self.streams = streams

    def write(self, text: str) -> int:
        """같은 텍스트를 모든 대상에 쓰고 즉시 보이도록 버퍼를 비운다."""

        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self) -> None:
        """등록된 모든 출력 대상에 남아 있는 텍스트를 실제로 반영한다."""

        for stream in self.streams:
            stream.flush()


async def run_pipeline() -> None:
    """수집부터 성능 비교까지 핵심 업무 다섯 단계를 순서대로 수행한다.

    네트워크 수집 내부에서는 세 요청이 동시에 실행되지만, 품질이 확인되지 않은 값을
    저장할 수는 없으므로 이후 단계는 검증 완료 후 차례대로 진행한다. 중간 단계가
    실패하면 뒤 작업을 계속하지 않아 잘못된 결과를 정상 산출물처럼 남기지 않는다.
    """

    # 1단계: 전체 구간을 재서 세 API 동시 수집의 실제 완료 시간을 공개한다.
    print("[1/5] 세 API 동시 수집 시작 (하나의 AsyncClient + asyncio.gather)")
    started = perf_counter()
    collected = await collect_all()
    elapsed = perf_counter() - started
    print(f"      수집 및 Pydantic 검증 완료: {elapsed:.3f}초")
    print(
        "      실제 응답: "
        f"Open-Meteo {len(collected.weather.hourly.time)}시간, "
        f"Countries.dev {collected.country.name}, "
        f"ip-api {collected.ip_location.query} -> {collected.ip_location.city}"
    )

    # 2단계: 중첩된 API 응답을 행과 열이 있는 세 업무용 표로 바꾼다.
    print("[2/5] 검증 모델을 API별 표로 정규화")
    datasets = normalize(collected)
    print("      " + ", ".join(f"{name}={len(frame)}행" for name, frame in datasets.items()))

    # 3단계: 같은 논리 데이터를 두 형식으로 보관하고 실제 파일을 다시 열어 검사한다.
    print("[3/5] CSV 및 Parquet 저장 후 전체 데이터 재읽기 검증")
    paths = save_and_verify(datasets, OUTPUT_ROOT)
    for name, (csv_path, parquet_path) in paths.items():
        print(f"      {name}: {csv_path.relative_to(PROJECT_ROOT)}")
        print(f"             {parquet_path.relative_to(PROJECT_ROOT)}")
    print("      행 수, 열, 주요 값을 포함한 전체 값 일치 확인 완료")

    # 4단계: 단발성 수치가 아닌 20회 평균과 최종 파일 크기로 형식을 비교한다.
    print("[4/5] CSV/Parquet 읽기·쓰기 성능 20회 반복 측정")
    result = benchmark_formats(datasets, OUTPUT_ROOT, repeats=20)
    printable = result.table.copy()
    printable["avg_write_ms"] = printable["avg_write_ms"].map(lambda value: f"{value:.3f}")
    printable["avg_read_ms"] = printable["avg_read_ms"].map(lambda value: f"{value:.3f}")
    print(printable.to_string(index=False))
    print(f"      결과 파일: {(OUTPUT_ROOT / 'performance_results.csv').relative_to(PROJECT_ROOT)}")
    print(f"      분석: {result.analysis}")

    # 이 문구까지 출력되어야 수집·검증·저장·성능 측정이 모두 성공한 것이다.
    print("[5/5] 파이프라인 전체 작업 완료")


def main() -> None:
    """로그 폴더를 준비하고 비동기 파이프라인을 안전하게 실행한다.

    ``redirect_stdout``이 파이프라인의 모든 화면 출력을 ``Tee``로 보내므로 터미널과
    ``run_output.txt``의 내용이 일치한다. 파일은 UTF-8로 저장해 한국어가 깨지지 않는다.
    """

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = REPORT_ROOT / "run_output.txt"
    with (
        log_path.open("w", encoding="utf-8") as log_file,
        redirect_stdout(Tee(sys.stdout, log_file)),
    ):
        asyncio.run(run_pipeline())
        print(f"실행 로그 저장: {log_path.relative_to(PROJECT_ROOT)}")


# 다른 테스트 코드에서 이 모듈을 불러올 때는 자동 실행하지 않고, 사용자가 모듈 실행
# 명령을 입력했을 때만 전체 파이프라인을 시작한다.
if __name__ == "__main__":
    main()
