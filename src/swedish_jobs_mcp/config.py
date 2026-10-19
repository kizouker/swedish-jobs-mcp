"""Settings for the Swedish Jobs MCP server.

Every value can be overridden with an environment variable, so nothing has to
be changed in the code. Values are read once, when the server starts.

    SWEDISH_JOBS_MAX_PAGE_SIZE      most ads returned per call          (default 25)
    SWEDISH_JOBS_DEFAULT_PAGE_SIZE  ads returned when no limit is given (default 25)
    SWEDISH_JOBS_TIMEOUT            seconds to wait for JobSearch       (default 30)
    SWEDISH_JOBS_STREAM_TIMEOUT     seconds to wait for JobStream       (default 120)
    JOBTECH_SEARCH_URL             base URL of the JobSearch API
    JOBTECH_STREAM_URL             base URL of the JobStream API
"""

import os


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Read an integer setting. A missing or invalid value gives the default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


SEARCH_URL = os.environ.get("JOBTECH_SEARCH_URL", "https://jobsearch.api.jobtechdev.se")
STREAM_URL = os.environ.get("JOBTECH_STREAM_URL", "https://jobstream.api.jobtechdev.se")

MAX_PAGE_SIZE = _int_env("SWEDISH_JOBS_MAX_PAGE_SIZE", 25)
# The default page can never be larger than the maximum page.
DEFAULT_PAGE_SIZE = min(_int_env("SWEDISH_JOBS_DEFAULT_PAGE_SIZE", 25), MAX_PAGE_SIZE)

SEARCH_TIMEOUT = _int_env("SWEDISH_JOBS_TIMEOUT", 30)
STREAM_TIMEOUT = _int_env("SWEDISH_JOBS_STREAM_TIMEOUT", 120)
