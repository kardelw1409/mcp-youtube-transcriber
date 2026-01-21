# mcp-youtube-transcriber

`youtube_transcriber` is a minimal MCP server that provides:

- `search_videos(query, limit=5, sort="relevance")` – YouTube search via `yt-dlp`
- `get_transcript(url_or_id, lang="en", prefer_auto=True, include_timestamps=False)` – transcripts via `youtube-transcript-api`

It is intentionally “dumb”: no RAG, no summarization, no music analysis. It just returns metadata and transcripts.

## Features

- Fast, flat YouTube search using `yt-dlp` with `ytsearch`/`ytsearchdate`.
- Transcript retrieval for videos with subtitles enabled, with manual vs auto selection.
- Optional timestamped segments.
- Optional metadata enrichment (title, channel, duration, upload date) on transcript calls.
- Stdio MCP transport compatible with Codex / MCP hosts.

## Requirements

- Windows
- Python 3.11+

## Installation

```powershell
cd C:\repos\mcp-youtube-transcriber
py -3.11 -m venv .venv
.\.venv\Scripts\activate
py -m pip install -U pip
pip install -e .
```

This installs dependencies:

- `mcp[cli]`
- `yt-dlp`
- `youtube-transcript-api`

## Running the server (module entrypoint)

The server is exposed as a module entrypoint so you can run:

```powershell
cd C:\repos\mcp-youtube-transcriber
.\.venv\Scripts\activate
py -m mcp_youtube_transcriber
```

The process will start an MCP server on stdio and wait for an MCP host. It logs only to stderr and does not print to stdout.

## MCP tools

### `search_videos`

```python
search_videos(query: str, limit: int = 5, sort: str = "relevance") -> dict
```

- `query` – search query string.
- `limit` – number of items (1..10, clamped).
- `sort` – `"relevance"`, `"views"`, or `"date"`.

Behavior:

- Uses `yt-dlp` with `default_search="ytsearch"` and `extract_flat=True`.
- For `"relevance"` and `"views"` it uses `ytsearch{limit}:{query}`.
- For `"date"` it uses `ytsearchdate{limit}:{query}`.
- Returns:
  - `query`, `limit`, `sort_requested`, `sort_effective`
  - `items`: list of:
    - `video_id`
    - `title`
    - `channel`
    - `duration_seconds`
    - `upload_date` (YYYYMMDD if available)
    - `url`
    - `view_count` (if available)

If `"views"` is requested but view counts are not available, `sort_effective` is set to `"relevance"`.

### `get_transcript`

```python
get_transcript(
    url_or_id: str,
    lang: str = "en",
    prefer_auto: bool = True,
    include_timestamps: bool = False,
) -> dict
```

- `url_or_id` – full YouTube URL, short `youtu.be` URL, `shorts` URL, or raw 11-char ID.
- `lang` – language code (e.g. `"en"`, `"ru"`, etc.).
- `prefer_auto` – if `True`, prefer auto-generated transcripts; otherwise prefer manually provided ones.
- `include_timestamps` – if `True`, include a `segments` array.

Behavior:

- Uses `youtube-transcript-api`:
  - If `prefer_auto=True`: try generated transcript first, then manual.
  - If `prefer_auto=False`: try manual transcript first, then generated.
- On success returns:
  - `video_id`
  - `language`
  - `is_auto_generated`
  - `transcript_text` – a single concatenated string
  - `segments` – optional, when `include_timestamps=True`:
    - `{start: float, duration: float, text: str}`
  - `metadata` (when available):
    - `video_id`, `title`, `channel`, `upload_date`, `duration_seconds`, `view_count`, `url`
- If no transcript is available:
  - `error` set to `"No transcript available"` or a more specific message (e.g. `"No transcript available (disabled)"`).

## Internal helpers

`mcp_youtube_transcriber.utils` contains:

- `extract_video_id(url_or_id: str) -> str` – supports `watch?v=`, `youtu.be/`, `shorts/`, and raw IDs.
- `get_metadata(url_or_id: str) -> dict | None` – optional helper used by `get_transcript` to enrich results.

## Testing instructions

1. **Create and activate venv (Windows / PowerShell)**:

```powershell
cd C:\repos\mcp-youtube-transcriber
py -3.11 -m venv .venv
.\.venv\Scripts\activate
py -m pip install -U pip
pip install -e .
```

2. **Quick CLI sanity test (without MCP host)**:

```powershell
cd C:\repos\mcp-youtube-transcriber
.\.venv\Scripts\activate
py -m mcp_youtube_transcriber
```

The process will wait on stdio for an MCP host. You should not see any stdout output, only possible logs on stderr.

3. **Minimal local function test (optional)**:

```powershell
cd C:\repos\mcp-youtube-transcriber
.\.venv\Scripts\activate
python - << "PYCODE"
from mcp_youtube_transcriber.utils import extract_video_id
print("ID:", extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
PYCODE
```

This should print the extracted video ID to the terminal (this is outside the MCP stdio protocol, just for quick sanity checking).

## MCP host config example (Codex / config.toml)

For Codex, you can add an MCP server entry similar to:

```toml
[mcp_servers.youtube_transcriber]
command = "C:\\repos\\mcp-youtube-transcriber\\.venv\\Scripts\\python.exe"
args = ["-m", "mcp_youtube_transcriber"]
```

This will launch the server from the local `.venv` using the module entrypoint. Make sure the venv exists and the package is installed (`pip install -e .`) before starting Codex.

