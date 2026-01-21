import logging
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)
from yt_dlp import YoutubeDL

from .utils import extract_video_id, get_metadata


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("youtube_transcriber")


@mcp.tool()
async def search_videos(
    query: str,
    limit: int = 5,
    sort: str = "relevance",
) -> Dict[str, Any]:
    """
    Search YouTube videos by query using yt-dlp.
    Returns a list of basic metadata entries.
    """

    limit = max(1, min(limit, 10))
    sort = sort or "relevance"
    sort = sort.lower()
    if sort not in {"relevance", "views", "date"}:
        sort = "relevance"

    if sort == "date":
        search_query = f"ytsearchdate{limit}:{query}"
    else:
        # For relevance and views we both use ytsearch; views may not be strictly sorted.
        search_query = f"ytsearch{limit}:{query}"

    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "default_search": "ytsearch",
    }

    items: List[Dict[str, Any]] = []
    used_sort = sort

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
    except Exception as exc:  # pragma: no cover - network errors
        logger.error("search_videos failed: %s", exc)
        return {
            "query": query,
            "limit": limit,
            "items": [],
            "error": str(exc),
        }

    if not isinstance(info, dict):
        return {"query": query, "limit": limit, "items": []}

    entries = info.get("entries") or []
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        if not video_id:
            continue
        url = f"https://www.youtube.com/watch?v={video_id}"
        items.append(
            {
                "video_id": video_id,
                "title": entry.get("title"),
                "channel": entry.get("uploader") or entry.get("channel"),
                "duration_seconds": entry.get("duration"),
                "upload_date": entry.get("upload_date"),
                "url": url,
                "view_count": entry.get("view_count"),
            }
        )

    # If sort == "views" but view_count is not available, note that we effectively used relevance.
    if sort == "views":
        if not any(item.get("view_count") for item in items):
            used_sort = "relevance"

    return {
        "query": query,
        "limit": limit,
        "sort_requested": sort,
        "sort_effective": used_sort,
        "items": items,
    }


@mcp.tool()
async def get_transcript(
    url_or_id: str,
    lang: str = "en",
    prefer_auto: bool = True,
    include_timestamps: bool = False,
) -> Dict[str, Any]:
    """
    Fetch a YouTube transcript by video URL or ID.
    """

    video_id = extract_video_id(url_or_id)
    languages = [lang]

    try:
        transcripts = YouTubeTranscriptApi().list(video_id)
    except TranscriptsDisabled:
        return {
            "video_id": video_id,
            "language": lang,
            "is_auto_generated": None,
            "transcript_text": "",
            "error": "No transcript available (disabled)",
        }
    except Exception as exc:  # pragma: no cover - network/api errors
        return {
            "video_id": video_id,
            "language": lang,
            "is_auto_generated": None,
            "transcript_text": "",
            "error": str(exc),
        }

    transcript_obj = None
    is_auto_generated = None

    try:
        if prefer_auto:
            try:
                transcript_obj = transcripts.find_generated_transcript(languages)
                is_auto_generated = True
            except NoTranscriptFound:
                transcript_obj = transcripts.find_transcript(languages)
                is_auto_generated = False
        else:
            try:
                transcript_obj = transcripts.find_transcript(languages)
                is_auto_generated = False
            except NoTranscriptFound:
                transcript_obj = transcripts.find_generated_transcript(languages)
                is_auto_generated = True
    except NoTranscriptFound:
        return {
            "video_id": video_id,
            "language": lang,
            "is_auto_generated": None,
            "transcript_text": "",
            "error": "No transcript available",
        }

    segments = transcript_obj.fetch()

    def _seg_text(seg: Any) -> str:
        if isinstance(seg, dict):
            return seg.get("text", "")
        return getattr(seg, "text", "") or ""

    def _seg_start(seg: Any) -> float:
        if isinstance(seg, dict):
            return float(seg.get("start", 0.0))
        return float(getattr(seg, "start", 0.0) or 0.0)

    def _seg_duration(seg: Any) -> float:
        if isinstance(seg, dict):
            return float(seg.get("duration", 0.0))
        return float(getattr(seg, "duration", 0.0) or 0.0)

    transcript_text = " ".join(
        _seg_text(seg).strip() for seg in segments if _seg_text(seg).strip()
    )

    result: Dict[str, Any] = {
        "video_id": video_id,
        "language": transcript_obj.language or lang,
        "is_auto_generated": bool(is_auto_generated),
        "transcript_text": transcript_text,
    }

    if include_timestamps:
        result["segments"] = [
            {
                "start": _seg_start(seg),
                "duration": _seg_duration(seg),
                "text": _seg_text(seg),
            }
            for seg in segments
        ]

    # Optional metadata enrichment
    metadata = get_metadata(video_id)
    if metadata:
        result["metadata"] = metadata

    return result


def main() -> None:
    logger.info("youtube_transcriber server ready")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

