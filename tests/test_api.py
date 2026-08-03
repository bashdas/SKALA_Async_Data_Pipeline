"""외부 API 장애와 데이터 오류가 이해 가능한 메시지로 보고되는지 확인한다.

작성자: 박다솔
작성목적: 실제 인터넷이나 공급자 상태와 무관하게, HTTP 장애와 응답 스키마 변경 시
          어느 API에서 무엇이 잘못됐는지 운영 담당자가 식별할 수 있음을 보장한다.
작성일: 2026-08-03
"""

import asyncio

import httpx
import pytest

from src.data_pipeline.api import ApiCollectionError, ApiValidationError, fetch_country


def test_http_error_names_api() -> None:
    """Countries.dev가 503 장애를 반환하면 서비스명과 상태 코드가 표시되어야 한다."""

    # MockTransport는 실제 인터넷 대신 아래 고정 응답을 돌려주는 모의 API다.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ApiCollectionError, match="Countries.dev.*503"):
                await fetch_country(client)

    asyncio.run(exercise())


def test_validation_error_names_api_and_field() -> None:
    """필수 필드 누락 시 API 이름과 누락 필드명이 오류에 포함되어야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        # 국가 코드는 있지만 필수 국가명 등이 없는 의도적인 불완전 응답이다.
        return httpx.Response(200, request=request, json={"alpha3Code": "KOR"})

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ApiValidationError, match="Countries.dev.*name"):
                await fetch_country(client)

    asyncio.run(exercise())
