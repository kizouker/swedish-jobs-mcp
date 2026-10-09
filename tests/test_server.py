"""Offline tests: the JobTech API is faked with respx, so these run anywhere."""

import asyncio

import httpx
import pytest
import respx

from platsbanken_mcp import server
from platsbanken_mcp.server import (
    SEARCH_URL,
    STREAM_URL,
    changes_since,
    get_job,
    search_jobs,
    suggest_search_terms,
)

AD = {
    "id": "123",
    "headline": "Python-utvecklare",
    "employer": {"name": "Acme AB"},
    "workplace_address": {"municipality": "Malmö", "region": "Skåne län"},
    "occupation": {"label": "Mjukvaruutvecklare"},
    "publication_date": "2026-10-01T08:00:00",
    "application_deadline": "2026-10-31T23:59:59",
    "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/123",
}


def test_all_tools_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"search_jobs", "get_job", "suggest_search_terms", "changes_since"}


@respx.mock
async def test_search_jobs_maps_results():
    route = respx.get(f"{SEARCH_URL}/search").mock(
        return_value=httpx.Response(200, json={"total": {"value": 42}, "hits": [AD]})
    )
    result = await search_jobs(query="python malmö", limit=5)

    assert result["total"] == 42
    assert result["ads"][0] == {
        "id": "123",
        "title": "Python-utvecklare",
        "employer": "Acme AB",
        "municipality": "Malmö",
        "region": "Skåne län",
        "occupation": "Mjukvaruutvecklare",
        "published": "2026-10-01T08:00:00",
        "deadline": "2026-10-31T23:59:59",
        "url": "https://arbetsformedlingen.se/platsbanken/annonser/123",
    }
    params = route.calls.last.request.url.params
    assert params["q"] == "python malmö"
    assert params["limit"] == "5"


@respx.mock
async def test_search_jobs_clamps_limit_and_adds_filters():
    route = respx.get(f"{SEARCH_URL}/search").mock(
        return_value=httpx.Response(200, json={"total": {"value": 0}, "hits": []})
    )
    await search_jobs(limit=500, offset=-3, remote_only=True, published_within_days=7)

    params = route.calls.last.request.url.params
    assert params["limit"] == "100"
    assert params["offset"] == "0"
    assert params["remote"] == "true"
    assert "published-after" in params
    assert "q" not in params  # empty query is left out


@respx.mock
async def test_api_key_is_sent_when_set(monkeypatch):
    monkeypatch.setenv("JOBTECH_API_KEY", "secret-test-key")
    route = respx.get(f"{SEARCH_URL}/search").mock(
        return_value=httpx.Response(200, json={"total": {"value": 0}, "hits": []})
    )
    await search_jobs(query="x")
    assert route.calls.last.request.headers["api-key"] == "secret-test-key"


@respx.mock
async def test_get_job_includes_details():
    full = {
        **AD,
        "description": {"text": "Vi söker en utvecklare."},
        "employment_type": {"label": "Vanlig anställning"},
        "working_hours_type": {"label": "Heltid"},
        "salary_description": "Fast lön",
        "application_details": {"url": "https://acme.example/apply"},
        "must_have": {"skills": [{"label": "Python"}, {"label": "Git"}]},
    }
    respx.get(f"{SEARCH_URL}/ad/123").mock(return_value=httpx.Response(200, json=full))

    job = await get_job("123")
    assert job["description"] == "Vi söker en utvecklare."
    assert job["working_hours"] == "Heltid"
    assert job["apply_url"] == "https://acme.example/apply"
    assert job["must_have_skills"] == ["Python", "Git"]


@respx.mock
async def test_get_job_handles_missing_fields():
    respx.get(f"{SEARCH_URL}/ad/9").mock(return_value=httpx.Response(200, json={"id": "9"}))
    job = await get_job("9")
    assert job["id"] == "9"
    assert job["description"] is None
    assert job["must_have_skills"] == []


@respx.mock
async def test_suggest_search_terms():
    respx.get(f"{SEARCH_URL}/complete").mock(
        return_value=httpx.Response(
            200, json={"typeahead": [{"value": "utvecklare"}, {"value": "utvecklingsledare"}]}
        )
    )
    assert await suggest_search_terms("utve") == ["utvecklare", "utvecklingsledare"]


@respx.mock
async def test_changes_since_splits_added_and_removed():
    respx.get(f"{STREAM_URL}/stream").mock(
        return_value=httpx.Response(
            200, json=[AD, {"id": "999", "removed": True}, {**AD, "id": "124"}]
        )
    )
    result = await changes_since(hours=24, max_ads=1)
    assert result["added_count"] == 2
    assert result["removed_count"] == 1
    assert len(result["added"]) == 1  # max_ads respected


@respx.mock
async def test_http_errors_are_raised():
    respx.get(f"{SEARCH_URL}/search").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await search_jobs(query="x")
