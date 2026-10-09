"""
platsbanken_mcp.py — an MCP server for Swedish job ads (Platsbanken).

Data comes from Arbetsförmedlingen's open JobTech APIs:
  - JobSearch  https://jobsearch.api.jobtechdev.se  (search + single ad)
  - JobStream  https://jobstream.api.jobtechdev.se  (all changes since a time)

An API key is optional but recommended (free): https://apirequest.jobtechdev.se/
Set it in the environment as JOBTECH_API_KEY.

Run:  uvx platsbanken-mcp
"""

import os
from datetime import datetime, timedelta

import httpx
from mcp.server.fastmcp import FastMCP

SEARCH_URL = "https://jobsearch.api.jobtechdev.se"
STREAM_URL = "https://jobstream.api.jobtechdev.se"

mcp = FastMCP("platsbanken")


def _headers() -> dict:
    h = {"accept": "application/json"}
    key = os.environ.get("JOBTECH_API_KEY")
    if key:
        h["api-key"] = key
    return h


def _short(ad: dict) -> dict:
    """Turn one raw ad into a small, readable summary."""
    addr = ad.get("workplace_address") or {}
    return {
        "id": ad.get("id"),
        "title": ad.get("headline"),
        "employer": (ad.get("employer") or {}).get("name"),
        "municipality": addr.get("municipality"),
        "region": addr.get("region"),
        "occupation": (ad.get("occupation") or {}).get("label"),
        "published": ad.get("publication_date"),
        "deadline": ad.get("application_deadline"),
        "url": ad.get("webpage_url"),
    }


@mcp.tool()
async def search_jobs(
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    remote_only: bool = False,
    published_within_days: int | None = None,
    sort: str = "relevance",
) -> dict:
    """Search all current job ads in Sweden (Platsbanken).

    query: free text, e.g. "python malmö", "devops", "elektriker Lund".
           Quotes for phrases, * for prefix (e.g. "utvecklar*").
    limit: 1-100 ads per page. offset: for paging.
    published_within_days: only ads published in the last N days.
    sort: relevance | pubdate-desc | pubdate-asc | applydate-desc | applydate-asc
    """
    params: dict = {
        "q": query or None,
        "limit": max(1, min(limit, 100)),
        "offset": max(0, offset),
        "sort": sort,
    }
    if remote_only:
        params["remote"] = "true"
    if published_within_days:
        since = datetime.now() - timedelta(days=published_within_days)
        params["published-after"] = since.strftime("%Y-%m-%dT%H:%M:%S")
    params = {k: v for k, v in params.items() if v is not None}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{SEARCH_URL}/search", params=params, headers=_headers())
        r.raise_for_status()
        data = r.json()

    return {
        "total": (data.get("total") or {}).get("value"),
        "offset": params["offset"],
        "ads": [_short(ad) for ad in data.get("hits", [])],
    }


@mcp.tool()
async def get_job(ad_id: str) -> dict:
    """Get one job ad in full, including the description text."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{SEARCH_URL}/ad/{ad_id}", headers=_headers())
        r.raise_for_status()
        ad = r.json()

    out = _short(ad)
    out["description"] = (ad.get("description") or {}).get("text")
    out["employment_type"] = (ad.get("employment_type") or {}).get("label")
    out["working_hours"] = (ad.get("working_hours_type") or {}).get("label")
    out["salary"] = ad.get("salary_description")
    out["apply_url"] = (ad.get("application_details") or {}).get("url")
    out["must_have_skills"] = [
        s.get("label") for s in (ad.get("must_have") or {}).get("skills", [])
    ]
    return out


@mcp.tool()
async def suggest_search_terms(text: str) -> list[str]:
    """Autocomplete a search word (e.g. "utve" -> "utvecklare", ...)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{SEARCH_URL}/complete", params={"q": text}, headers=_headers()
        )
        r.raise_for_status()
        data = r.json()
    return [t.get("value") for t in data.get("typeahead", [])]


@mcp.tool()
async def changes_since(hours: int = 24, max_ads: int = 200) -> dict:
    """List ads that were added or removed in the last N hours (all of Sweden).

    Uses JobStream. Good for "what's new today?" — not for keyword search.
    """
    since = datetime.now() - timedelta(hours=hours)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(
            f"{STREAM_URL}/stream",
            params={"date": since.strftime("%Y-%m-%dT%H:%M:%S")},
            headers=_headers(),
        )
        r.raise_for_status()
        ads = r.json()

    removed = [a.get("id") for a in ads if a.get("removed")]
    added = [_short(a) for a in ads if not a.get("removed")]
    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "added": added[:max_ads],
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
