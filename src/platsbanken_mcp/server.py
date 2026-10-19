"""
platsbanken_mcp — an MCP server for Swedish job ads (Platsbanken).

Data comes from Arbetsförmedlingen's open JobTech APIs:
  - JobSearch  https://jobsearch.api.jobtechdev.se  (search + single ad)
  - JobStream  https://jobstream.api.jobtechdev.se  (all changes since a time)

Settings (page sizes, timeouts, URLs) live in config.py.

Run:  uvx platsbanken-mcp
"""

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SEARCH_TIMEOUT,
    SEARCH_URL,
    STREAM_TIMEOUT,
    STREAM_URL,
)

SortOrder = Literal[
    "relevance", "pubdate-desc", "pubdate-asc", "applydate-desc", "applydate-asc"
]

# These two are shown to the AI in the tool schema, so they are built from the
# settings instead of being typed out by hand.
LimitParam = Annotated[
    int,
    Field(
        description=f"Ads per page, 1-{MAX_PAGE_SIZE}. "
        "For more ads, call again with offset=next_offset."
    ),
]
OffsetParam = Annotated[
    int,
    Field(description="Ads to skip. Use next_offset from the previous result."),
]

mcp = FastMCP("platsbanken")

HEADERS = {"accept": "application/json"}


def clamp_limit(limit: int) -> int:
    """Keep a page size between 1 and MAX_PAGE_SIZE."""
    return max(1, min(limit, MAX_PAGE_SIZE))


def next_offset(offset: int, returned: int, total: int | None) -> int | None:
    """Where the next page starts, or None when there are no more ads."""
    following = offset + returned
    if returned == 0 or total is None or following >= total:
        return None
    return following


async def _get_json(
    base_url: str, path: str, *, params: dict | None = None, timeout: float
) -> Any:
    """GET one JSON document from a JobTech API."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{base_url}{path}", params=params, headers=HEADERS)
        r.raise_for_status()
        return r.json()


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
    limit: LimitParam = DEFAULT_PAGE_SIZE,
    offset: OffsetParam = 0,
    remote_only: bool = False,
    published_within_days: int | None = None,
    sort: SortOrder = "relevance",
) -> dict:
    """Search all current job ads in Sweden (Platsbanken).

    query: free text, e.g. "python malmö", "devops", "elektriker Lund".
           Quotes for phrases, * for prefix (e.g. "utvecklar*").
    published_within_days: only ads published in the last N days.

    Returns total, returned, offset, next_offset and ads.
    If next_offset is null, there are no more ads.
    """
    offset = max(0, offset)
    params: dict = {
        "q": query or None,
        "limit": clamp_limit(limit),
        "offset": offset,
        "sort": sort,
    }
    if remote_only:
        params["remote"] = "true"
    if published_within_days:
        since = datetime.now() - timedelta(days=published_within_days)
        params["published-after"] = since.strftime("%Y-%m-%dT%H:%M:%S")
    params = {k: v for k, v in params.items() if v is not None}

    data = await _get_json(SEARCH_URL, "/search", params=params, timeout=SEARCH_TIMEOUT)

    hits = data.get("hits", [])
    total = (data.get("total") or {}).get("value")
    return {
        "total": total,
        "returned": len(hits),
        "offset": offset,
        "next_offset": next_offset(offset, len(hits), total),
        "ads": [_short(ad) for ad in hits],
    }


@mcp.tool()
async def get_job(ad_id: str) -> dict:
    """Get one job ad in full, including the description text."""
    ad = await _get_json(SEARCH_URL, f"/ad/{ad_id}", timeout=SEARCH_TIMEOUT)

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
    data = await _get_json(
        SEARCH_URL, "/complete", params={"q": text}, timeout=SEARCH_TIMEOUT
    )
    return [t.get("value") for t in data.get("typeahead", [])]


@mcp.tool()
async def changes_since(
    hours: int = 24,
    limit: LimitParam = DEFAULT_PAGE_SIZE,
    offset: OffsetParam = 0,
) -> dict:
    """List ads that were added or removed in the last N hours (all of Sweden).

    Uses JobStream. Good for "what's new today?" — not for keyword search.

    Returns added_count, removed_count, returned, offset, next_offset and added.
    If next_offset is null, there are no more added ads.
    """
    offset = max(0, offset)
    since = datetime.now() - timedelta(hours=max(1, hours))
    ads = await _get_json(
        STREAM_URL,
        "/stream",
        params={"date": since.strftime("%Y-%m-%dT%H:%M:%S")},
        timeout=STREAM_TIMEOUT,
    )

    removed = [a for a in ads if a.get("removed")]
    added = [_short(a) for a in ads if not a.get("removed")]
    page = added[offset : offset + clamp_limit(limit)]
    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "returned": len(page),
        "offset": offset,
        "next_offset": next_offset(offset, len(page), len(added)),
        "added": page,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
