"""
Competitor verifier tests — mocked HTTP to avoid real network calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.schemas import Competitor
from app.services.competitor_verifier import verify_competitor_domains


def make_competitor(name: str, domain: str | None) -> Competitor:
    return Competitor(
        name=name,
        domain=domain,
        main_service="Test service",
        target_market="Test market",
        strengths=["Strength 1"],
        weaknesses_or_gaps=["Weakness 1"],
    )


@pytest.mark.asyncio
async def test_no_domain_sets_verified_null():
    comp = make_competitor("NoDomain Corp", None)
    results = await verify_competitor_domains([comp])
    assert results[0].domain_verified is None


@pytest.mark.asyncio
async def test_successful_domain_verification():
    comp = make_competitor("Google", "google.com")

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("app.services.competitor_verifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        results = await verify_competitor_domains([comp])

    assert results[0].domain_verified is True


@pytest.mark.asyncio
async def test_timeout_sets_verified_false():
    comp = make_competitor("Slow Corp", "slow.example.com")

    with patch("app.services.competitor_verifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        results = await verify_competitor_domains([comp])

    assert results[0].domain_verified is False


@pytest.mark.asyncio
async def test_ssrf_domain_blocked():
    """LLM-suggested domain that resolves to private IP must be blocked."""
    comp = make_competitor("Internal Corp", "192.168.1.1")
    results = await verify_competitor_domains([comp])
    assert results[0].domain_verified is False


@pytest.mark.asyncio
async def test_server_error_sets_verified_false():
    comp = make_competitor("Error Corp", "error.example.com")

    mock_response = MagicMock()
    mock_response.status_code = 503

    with patch("app.services.competitor_verifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        results = await verify_competitor_domains([comp])

    assert results[0].domain_verified is False


@pytest.mark.asyncio
async def test_parallel_verification():
    """Multiple competitors are verified in parallel."""
    competitors = [
        make_competitor("Alpha", "alpha.example.com"),
        make_competitor("Beta", "beta.example.com"),
        make_competitor("NoDomain", None),
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("app.services.competitor_verifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        results = await verify_competitor_domains(competitors)

    assert len(results) == 3
    assert results[2].domain_verified is None   # no domain
