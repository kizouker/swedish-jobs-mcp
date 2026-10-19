"""Offline tests: the JobTech API is faked with respx, so these run anywhere."""

import asyncio

import httpx
import pytest
import respx

from platsbanken_mcp import server
from platsbanken_mcp.config import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SEARCH_URL,
    STREAM_URL,
    _int_env,
)
from platsbanken_mcp.server import (
    changes_since,
    clamp_limit,
    get_job,
    next_offset,
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
    assert result["returned"] == 1
    assert result["offset"] == 0
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
    assert params["limit"] == str(MAX_PAGE_SIZE)
    assert params["offset"] == "0"
    assert params["remote"] == "true"
    assert "published-after" in params
    assert "q" not in params  # empty query is left out


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
    result = await changes_since(hours=24, limit=1)
    assert result["added_count"] == 2
    assert result["removed_count"] == 1
    assert len(result["added"]) == 1  # limit respected


@respx.mock
async def test_http_errors_are_raised():
    respx.get(f"{SEARCH_URL}/search").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await search_jobs(query="x")


# --- paging -----------------------------------------------------------------


def test_clamp_limit_stays_within_bounds():
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(MAX_PAGE_SIZE) == MAX_PAGE_SIZE
    assert clamp_limit(MAX_PAGE_SIZE + 1) == MAX_PAGE_SIZE
    assert clamp_limit(10**6) == MAX_PAGE_SIZE


def test_default_page_size_never_exceeds_max():
    assert 1 <= DEFAULT_PAGE_SIZE <= MAX_PAGE_SIZE


def test_next_offset():
    assert next_offset(0, 25, 80) == 25
    assert next_offset(50, 25, 80) == 75
    assert next_offset(75, 5, 80) is None  # last page
    assert next_offset(0, 25, 25) is None  # everything fit on one page
    assert next_offset(0, 0, 80) is None  # empty page: never loop forever
    assert next_offset(0, 25, None) is None  # API gave no total


@respx.mock
async def test_search_jobs_pages_through_all_results():
    """Follow next_offset until it is None, like an AI client would."""
    total = MAX_PAGE_SIZE * 2 + 3
    ads = [{**AD, "id": str(i)} for i in range(total)]

    def answer(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["offset"])
        size = int(request.url.params["limit"])
        return httpx.Response(
            200, json={"total": {"value": total}, "hits": ads[start : start + size]}
        )

    respx.get(f"{SEARCH_URL}/search").mock(side_effect=answer)

    seen: list[str] = []
    offset: int | None = 0
    calls = 0
    while offset is not None:
        page = await search_jobs(query="x", limit=MAX_PAGE_SIZE, offset=offset)
        seen += [a["id"] for a in page["ads"]]
        offset = page["next_offset"]
        calls += 1

    assert seen == [str(i) for i in range(total)]  # nothing missing, nothing twice
    assert calls == 3


@respx.mock
async def test_search_jobs_last_page_has_no_next_offset():
    respx.get(f"{SEARCH_URL}/search").mock(
        return_value=httpx.Response(200, json={"total": {"value": 1}, "hits": [AD]})
    )
    result = await search_jobs(query="x")
    assert result["next_offset"] is None


@respx.mock
async def test_changes_since_pages_the_added_list():
    stream = [{**AD, "id": str(i)} for i in range(3)] + [{"id": "gone", "removed": True}]
    respx.get(f"{STREAM_URL}/stream").mock(return_value=httpx.Response(200, json=stream))

    first = await changes_since(limit=2, offset=0)
    assert [a["id"] for a in first["added"]] == ["0", "1"]
    assert first["next_offset"] == 2

    second = await changes_since(limit=2, offset=first["next_offset"])
    assert [a["id"] for a in second["added"]] == ["2"]
    assert second["next_offset"] is None
    assert second["removed_count"] == 1


# --- what the AI sees -------------------------------------------------------


def _tools_by_name():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_schema_tells_the_ai_the_real_page_limit():
    for name in ("search_jobs", "changes_since"):
        props = _tools_by_name()[name].inputSchema["properties"]
        assert f"1-{MAX_PAGE_SIZE}" in props["limit"]["description"]
        assert props["limit"]["default"] == DEFAULT_PAGE_SIZE


def test_schema_lists_valid_sort_orders():
    sort = _tools_by_name()["search_jobs"].inputSchema["properties"]["sort"]
    assert set(sort["enum"]) == {
        "relevance",
        "pubdate-desc",
        "pubdate-asc",
        "applydate-desc",
        "applydate-asc",
    }


# --- settings ---------------------------------------------------------------


def test_int_env_reads_and_falls_back(monkeypatch):
    monkeypatch.setenv("X_TEST", "40")
    assert _int_env("X_TEST", 25) == 40
    monkeypatch.setenv("X_TEST", "not-a-number")
    assert _int_env("X_TEST", 25) == 25
    monkeypatch.setenv("X_TEST", "0")
    assert _int_env("X_TEST", 25) == 1  # never below the minimum
    monkeypatch.delenv("X_TEST")
    assert _int_env("X_TEST", 25) == 25
