"""End-to-end tests: the server runs as a real process and we talk MCP to it.

This is what an AI client (Claude Desktop, Claude Code, ...) does:
start the server, list its tools, call them over stdio.

The JobTech APIs are replaced by a small fake web server on localhost, which the
server is pointed at with JOBTECH_SEARCH_URL / JOBTECH_STREAM_URL. No internet
needed, so these run anywhere, also in CI.
"""

import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.e2e

TOTAL_ADS = 60


def _ad(i: int) -> dict:
    return {
        "id": str(i),
        "headline": f"Utvecklare nr {i}",
        "employer": {"name": "Acme AB"},
        "workplace_address": {"municipality": "Malmö", "region": "Skåne län"},
        "occupation": {"label": "Mjukvaruutvecklare"},
        "publication_date": "2026-10-01T08:00:00",
        "application_deadline": "2026-10-31T23:59:59",
        "webpage_url": f"https://example.test/annonser/{i}",
    }


class FakeJobTech(BaseHTTPRequestHandler):
    """Just enough of JobSearch and JobStream for the tests."""

    requests: list[dict] = []  # every request seen: path, query, headers

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        FakeJobTech.requests.append(
            {"path": url.path, "query": query, "headers": dict(self.headers)}
        )

        if url.path == "/search":
            if query.get("q") == "boom":
                return self._send(500, {"error": "fake server error"})
            start, size = int(query["offset"]), int(query["limit"])
            hits = [_ad(i) for i in range(TOTAL_ADS)][start : start + size]
            return self._send(200, {"total": {"value": TOTAL_ADS}, "hits": hits})
        if url.path.startswith("/ad/"):
            ad_id = url.path.rsplit("/", 1)[1]
            return self._send(
                200,
                {**_ad(int(ad_id)), "description": {"text": "Beskrivning."}},
            )
        if url.path == "/complete":
            return self._send(200, {"typeahead": [{"value": "utvecklare"}]})
        if url.path == "/stream":
            ads = [_ad(i) for i in range(3)] + [{"id": "gone", "removed": True}]
            return self._send(200, ads)
        self._send(404, {"error": "not found"})


@pytest.fixture
def fake_api():
    FakeJobTech.requests = []
    httpd = HTTPServer(("127.0.0.1", 0), FakeJobTech)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@asynccontextmanager
async def connected_client(api_url: str, **extra_env: str):
    """Start the server as a subprocess and give back an MCP client session."""
    env = {
        **os.environ,
        "JOBTECH_SEARCH_URL": api_url,
        "JOBTECH_STREAM_URL": api_url,
        "NO_PROXY": "127.0.0.1,localhost",  # never send localhost through a proxy
        "no_proxy": "127.0.0.1,localhost",
        **extra_env,
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "platsbanken_mcp.server"], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _json(result) -> dict:
    """The JSON a tool returned."""
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


async def test_server_lists_all_tools(fake_api):
    async with connected_client(fake_api) as session:
        tools = await session.list_tools()
    assert {t.name for t in tools.tools} == {
        "search_jobs",
        "get_job",
        "suggest_search_terms",
        "changes_since",
    }


async def test_search_over_mcp_and_follow_next_offset(fake_api):
    async with connected_client(fake_api) as session:
        first = _json(await session.call_tool("search_jobs", {"query": "utvecklare"}))
        assert first["total"] == TOTAL_ADS
        assert first["returned"] == 25  # the default page
        assert first["next_offset"] == 25

        seen = [a["id"] for a in first["ads"]]
        offset = first["next_offset"]
        while offset is not None:
            page = _json(
                await session.call_tool(
                    "search_jobs", {"query": "utvecklare", "offset": offset}
                )
            )
            seen += [a["id"] for a in page["ads"]]
            offset = page["next_offset"]

    assert seen == [str(i) for i in range(TOTAL_ADS)]  # all 60, in order, once each


async def test_limit_above_the_cap_is_cut_down(fake_api):
    async with connected_client(fake_api) as session:
        result = _json(await session.call_tool("search_jobs", {"limit": 100}))

    assert result["returned"] == 25
    asked = [r for r in FakeJobTech.requests if r["path"] == "/search"][-1]
    assert asked["query"]["limit"] == "25"  # what really went to the API


async def test_cap_can_be_changed_with_an_environment_variable(fake_api):
    async with connected_client(fake_api, PLATSBANKEN_MAX_PAGE_SIZE="10") as session:
        result = _json(await session.call_tool("search_jobs", {"limit": 100}))
        schema = (await session.list_tools()).tools
    assert result["returned"] == 10
    limit_help = next(t for t in schema if t.name == "search_jobs").inputSchema[
        "properties"
    ]["limit"]["description"]
    assert "1-10" in limit_help  # the AI is told the new cap too


async def test_invalid_sort_is_rejected_before_calling_the_api(fake_api):
    async with connected_client(fake_api) as session:
        result = await session.call_tool("search_jobs", {"sort": "banana"})
    assert result.isError
    assert FakeJobTech.requests == []  # never reached the (fake) API


async def test_get_job_suggest_and_changes(fake_api):
    async with connected_client(fake_api) as session:
        job = _json(await session.call_tool("get_job", {"ad_id": "7"}))
        assert job["id"] == "7"
        assert job["description"] == "Beskrivning."

        words = await session.call_tool("suggest_search_terms", {"text": "utve"})
        assert "utvecklare" in words.content[0].text

        changes = _json(await session.call_tool("changes_since", {"limit": 2}))
        assert changes["added_count"] == 3
        assert changes["removed_count"] == 1
        assert changes["returned"] == 2
        assert changes["next_offset"] == 2



async def test_api_error_gives_a_tool_error_and_the_server_keeps_running(fake_api):
    async with connected_client(fake_api) as session:
        failed = await session.call_tool("search_jobs", {"query": "boom"})
        assert failed.isError

        # The server must survive the error and answer the next call.
        ok = _json(await session.call_tool("search_jobs", {"query": "fine"}))
        assert ok["total"] == TOTAL_ADS
