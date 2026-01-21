import logging
from typing import Any, Dict, Optional

from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)


def extract_video_id(url_or_id: str) -> str:
    """
    Extract a YouTube video ID from a URL or raw ID.
    Supports:
    - https://www.youtube.com/watch?v=...
    - https://youtu.be/...
    - https://www.youtube.com/shorts/...
    - raw 11-character ID
    """

    url_or_id = url_or_id.strip()

    # Raw 11-char ID heuristic
    if len(url_or_id) == 11 and "://" not in url_or_id and "/" not in url_or_id:
        return url_or_id

    # Basic parsing without importing urlparse to keep it lightweight
    lower = url_or_id.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        # youtu.be/<id>
        if "youtu.be/" in lower:
            return url_or_id.rsplit("/", 1)[-1].split("?", 1)[0]

        # shorts/<id>
        if "/shorts/" in lower:
            return url_or_id.split("/shorts/", 1)[-1].split("?", 1)[0]

        # watch?v=<id>
        if "watch?" in lower and "v=" in lower:
            query = url_or_id.split("?", 1)[-1]
            for part in query.split("&"):
                if part.startswith("v="):
                    return part.split("=", 1)[-1]

    # Fallback: last path segment as best guess
    if "/" in url_or_id:
        candidate = url_or_id.rstrip("/").rsplit("/", 1)[-1]
        if len(candidate) >= 6:
            return candidate

    return url_or_id


def safe_ydlp_extract(
    url_or_id: str,
    *,
    download: bool = False,
    extra_opts: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Wrapper around yt-dlp extraction with sensible defaults and error handling.
    Returns a metadata dict or None on error.
    """

    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": not download,
    }
    if extra_opts:
        ydl_opts.update(extra_opts)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_or_id, download=download)
        if isinstance(info, dict):
            return info
        return None
    except Exception as exc:  # pragma: no cover - network/yt-dlp errors
        logger.error("yt-dlp extract failed for %s: %s", url_or_id, exc)
        return None


def get_metadata(url_or_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch basic video metadata using yt-dlp.
    """

    video_id = extract_video_id(url_or_id)
    info = safe_ydlp_extract(f"https://www.youtube.com/watch?v={video_id}")
    if not info:
        return None

    return {
        "video_id": info.get("id") or video_id,
        "title": info.get("title"),
        "channel": info.get("uploader"),
        "upload_date": info.get("upload_date"),
        "duration_seconds": info.get("duration"),
        "view_count": info.get("view_count"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }

