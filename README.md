# platsbanken-mcp

An MCP server that lets AI assistants (Claude, Codex, Cursor, …) search all
current job ads in Sweden — the ads in Arbetsförmedlingen's Platsbanken.

It talks to Arbetsförmedlingen's open JobTech APIs. It is not affiliated with
or endorsed by Arbetsförmedlingen.

mcp-name: io.github.kizouker/platsbanken-mcp

## Tools

| Tool | What it does |
|---|---|
| `search_jobs` | Free-text search ("python malmö"), remote filter, last N days, sorting, paging |
| `get_job` | One ad in full: description, salary, skills, apply link |
| `suggest_search_terms` | Autocomplete ("utve" → "utvecklare") |
| `changes_since` | Ads added/removed in all of Sweden in the last N hours |

## Install

Needs [uv](https://docs.astral.sh/uv/).

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "platsbanken": {
      "command": "uvx",
      "args": ["platsbanken-mcp"],
      "env": { "JOBTECH_API_KEY": "your-key-here" }
    }
  }
}
```

**Claude Code:** `claude mcp add platsbanken -- uvx platsbanken-mcp`

**Codex** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.platsbanken]
command = "uvx"
args = ["platsbanken-mcp"]
```

## API key

The JobTech API asks users to register a free key at
https://apirequest.jobtechdev.se/ and send it as `JOBTECH_API_KEY`.
Use your own key — this server never ships one.

## Fair use

JobSearch is meant for searching, not bulk downloading. For a full copy of
all ads, JobTech points to the JobStream API instead. Job ad texts may
contain contact people's names and emails; don't store them longer than you need.

## Data

Job ads come from Arbetsförmedlingen / JobTech. Their terms of use apply to
the data; this repository's MIT license covers only the code.

## Development

```sh
uv run --extra dev pytest          # offline tests (API is faked)
uv run --extra dev pytest -m live  # live tests against the real JobTech API
```

GitHub Actions runs both on every push and every Monday, so a change in the
JobTech API shows up as a failed run.

## License

MIT
