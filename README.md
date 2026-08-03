# 비동기 데이터 수집 미니 파이프라인

Open-Meteo, Countries.dev, ip-api를 하나의 `httpx.AsyncClient`와
`asyncio.gather()`로 동시에 호출합니다. 실제 응답을 Pydantic v2로 검증한 뒤 API별
표로 정규화하고, 동일 데이터를 CSV와 Parquet으로 저장·재검증합니다. 마지막으로 두
형식의 읽기/쓰기 평균 시간과 파일 크기를 비교합니다.

## 환경 구성과 실행

Python 3.11 이상이 필요합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m src.data_pipeline.main
```

`.venv/bin/python -m src.data_pipeline.main` 한 명령이 수집, 검증, 정규화, 저장,
재읽기 검증,
20회 반복 성능 측정을 순서대로 수행합니다. 콘솔 출력은 동시에
`report/run_output.txt`에 기록됩니다. ip-api 무료 엔드포인트는 명세상 HTTP를
사용하므로, HTTPS만 허용하는 네트워크에서는 해당 요청이 차단될 수 있습니다.

가상환경 활성화를 선호한다면 아래와 같이 실행할 수도 있습니다.

```bash
source .venv/bin/activate
unalias python 2>/dev/null || true
python -m src.data_pipeline.main
```

현재 셸에서 `python` alias가 시스템 Python으로 고정되어 있으면 가상환경을 활성화해도
alias가 우선할 수 있습니다. `type -a python`으로 확인할 수 있으며, 가장 확실한 방법은
위처럼 `.venv/bin/python`을 직접 지정하는 것입니다.

## 검사

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
```

테스트는 고정 샘플만 사용하므로 외부 API 상태에 의존하지 않습니다.

## 프로젝트 구조

```text
.
├── README.md
├── requirements.txt
├── pyproject.toml
├── src/data_pipeline/
│   ├── api.py          # 비동기 HTTP 수집과 API별 검증
│   ├── models.py       # Pydantic v2 응답 모델
│   ├── storage.py      # 정규화, CSV/Parquet 저장 및 재검증
│   ├── benchmark.py    # 반복 성능 측정과 결과 분석
│   └── main.py         # 전체 실행 진입점과 로그 저장
├── tests/
│   ├── test_api.py
│   └── test_models.py
├── output/             # 실행 시 생성되며 Git에서 제외
│   ├── csv/
│   ├── parquet/
│   └── performance_results.csv
└── report/
    ├── run_output.txt
    └── report_draft.md
```

## 출력 데이터

- `weather`: 서울 3일 시간대별 기온과 강수확률
- `country`: 대한민국 국가 기본 정보
- `ip_location`: 8.8.8.8의 IP 기반 지역 정보

각 데이터셋은 `output/csv/`와 `output/parquet/`에 같은 논리 데이터로 저장됩니다.
저장 직후 다시 읽어 전체 행·열과 모든 값이 원본 DataFrame과 일치하는지 검사합니다.
