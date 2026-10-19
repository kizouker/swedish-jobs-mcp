"""Live tests against the real JobTech API.

Skipped by default. Run with:  pytest -m live
They check that the API still answers in the shape the server expects.
"""

import pytest

from platsbanken_mcp.server import get_job, search_jobs, suggest_search_terms

pytestmark = pytest.mark.live


async def test_live_search_returns_ads():
    result = await search_jobs(query="utvecklare", limit=3)
    assert result["total"] > 0
    assert 1 <= len(result["ads"]) <= 3
    ad = result["ads"][0]
    assert ad["id"] and ad["title"]
    assert ad["url"].startswith("https://")


async def test_live_get_job_has_description():
    first = (await search_jobs(query="utvecklare", limit=1))["ads"][0]
    job = await get_job(first["id"])
    assert job["id"] == first["id"]
    assert job["description"]


async def test_live_autocomplete():
    terms = await suggest_search_terms("utve")
    assert any(t.startswith("utve") for t in terms)


async def test_live_paging_gives_different_ads_on_each_page():
    first = await search_jobs(query="utvecklare", limit=3, offset=0)
    assert first["next_offset"] == 3  # there are far more than 3 developer ads
    second = await search_jobs(query="utvecklare", limit=3, offset=first["next_offset"])
    first_ids = {a["id"] for a in first["ads"]}
    assert first_ids.isdisjoint(a["id"] for a in second["ads"])
