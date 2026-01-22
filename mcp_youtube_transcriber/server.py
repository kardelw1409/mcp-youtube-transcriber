import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)
from yt_dlp import YoutubeDL

from .cache import DEFAULT_CACHE_PATH, CacheEntry, get_cache_entry, set_cache_entry
from .policy import detect_fetch_method, policy_for
from .throttle import (
    RequestThrottler,
    is_rate_limit_error,
    parse_retry_after_seconds,
)
from .utils import extract_video_id, get_metadata


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("youtube_transcriber")

FETCH_CLASSIFICATION = detect_fetch_method()
FETCH_POLICY = policy_for(FETCH_CLASSIFICATION)
THROTTLER = RequestThrottler(FETCH_POLICY)
CACHE_PATH = os.environ.get("YOUTUBE_TRANSCRIBER_CACHE_PATH", DEFAULT_CACHE_PATH)
INFLIGHT_LOCK = asyncio.Lock()
INFLIGHT: Dict[str, asyncio.Future] = {}


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
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Fetch a YouTube transcript by video URL or ID.
    """

    video_id = extract_video_id(url_or_id)
    languages = [lang]
    kind = "segments" if include_timestamps else "text"

    cache_entry: CacheEntry | None = None
    if not force_refresh:
        cache_entry = await asyncio.to_thread(
            get_cache_entry,
            video_id,
            lang,
            kind,
            cache_path=CACHE_PATH,
        )
        if cache_entry:
            logger.info(
                "cache hit video_id=%s lang=%s kind=%s age_s=%s",
                video_id,
                lang,
                kind,
                max(0, int(time.time() - cache_entry.fetched_at)),
            )
            return _format_cached_response(
                video_id=video_id,
                language=lang,
                include_timestamps=include_timestamps,
                cache_entry=cache_entry,
            )
        logger.info("cache miss video_id=%s lang=%s kind=%s", video_id, lang, kind)
    else:
        logger.info("cache bypass (force_refresh) video_id=%s lang=%s", video_id, lang)

    inflight_key = f"{video_id}:{lang}:{kind}"
    created = False
    future: asyncio.Future | None = None
    async with INFLIGHT_LOCK:
        future = INFLIGHT.get(inflight_key)
        if not future:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            INFLIGHT[inflight_key] = future
            created = True

    if future and not created:
        return await future
    if future is None:
        return _format_error_response(
            video_id=video_id,
            language=lang,
            error="Internal error: inflight tracking unavailable",
        )

    try:
        result = await _fetch_transcript_with_policy(
            video_id=video_id,
            languages=languages,
            lang=lang,
            prefer_auto=prefer_auto,
            include_timestamps=include_timestamps,
            kind=kind,
        )
        future.set_result(result)
        return result
    except Exception as exc:  # pragma: no cover - unexpected errors
        future.set_result(
            _format_error_response(
                video_id=video_id,
                language=lang,
                error=str(exc),
            )
        )
        return future.result()
    finally:
        async with INFLIGHT_LOCK:
            INFLIGHT.pop(inflight_key, None)


def _format_cache_meta(entry: CacheEntry | None) -> Dict[str, Any]:
    if entry:
        age = max(0, int(time.time() - entry.fetched_at))
        return {
            "hit": True,
            "age_seconds": age,
            "ttl_seconds": FETCH_POLICY.ttl_seconds,
            "method": entry.fetch_method,
        }
    return {
        "hit": False,
        "age_seconds": None,
        "ttl_seconds": FETCH_POLICY.ttl_seconds,
        "method": FETCH_POLICY.fetch_method,
    }


def _format_cached_response(
    *,
    video_id: str,
    language: str,
    include_timestamps: bool,
    cache_entry: CacheEntry,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "video_id": video_id,
        "language": language,
        "is_auto_generated": cache_entry.is_auto_generated,
        "transcript_text": cache_entry.transcript_text,
        "cache": _format_cache_meta(cache_entry),
        "source": "cache",
    }

    if include_timestamps and cache_entry.segments is not None:
        result["segments"] = cache_entry.segments
    if cache_entry.metadata:
        result["metadata"] = cache_entry.metadata

    return result


def _format_error_response(
    *,
    video_id: str,
    language: str,
    error: str,
) -> Dict[str, Any]:
    return {
        "video_id": video_id,
        "language": language,
        "is_auto_generated": None,
        "transcript_text": "",
        "error": error,
        "cache": _format_cache_meta(None),
        "source": "network",
    }


async def _fetch_transcript_with_policy(
    *,
    video_id: str,
    languages: List[str],
    lang: str,
    prefer_auto: bool,
    include_timestamps: bool,
    kind: str,
) -> Dict[str, Any]:
    attempt = 0
    while True:
        await THROTTLER.wait_for_slot()
        try:
            logger.info(
                "fetch attempt video_id=%s lang=%s kind=%s attempt=%s",
                video_id,
                lang,
                kind,
                attempt + 1,
            )
            result = await asyncio.to_thread(
                _fetch_transcript_once,
                video_id=video_id,
                languages=languages,
                lang=lang,
                prefer_auto=prefer_auto,
                include_timestamps=include_timestamps,
                kind=kind,
            )
            THROTTLER.register_success()
            return result
        except TranscriptsDisabled:
            THROTTLER.register_success()
            return _format_error_response(
                video_id=video_id,
                language=lang,
                error="No transcript available (disabled)",
            )
        except NoTranscriptFound:
            THROTTLER.register_success()
            return _format_error_response(
                video_id=video_id,
                language=lang,
                error="No transcript available",
            )
        except Exception as exc:  # pragma: no cover - network/api errors
            if is_rate_limit_error(exc):
                THROTTLER.register_rate_limit()
                retry_after = parse_retry_after_seconds(str(exc))
                logger.warning(
                    "rate limit video_id=%s lang=%s retry_after=%s attempt=%s",
                    video_id,
                    lang,
                    retry_after,
                    attempt + 1,
                )
                if attempt >= FETCH_POLICY.max_retries:
                    return _format_error_response(
                        video_id=video_id,
                        language=lang,
                        error="Rate limited (429): cooldown in effect",
                    )
                delay = retry_after or min(
                    FETCH_POLICY.backoff_max_seconds,
                    FETCH_POLICY.backoff_base_seconds * (2**attempt),
                )
                attempt += 1
                await asyncio.sleep(delay)
                continue
            logger.error(
                "fetch error video_id=%s lang=%s error=%s",
                video_id,
                lang,
                str(exc),
            )
            return _format_error_response(
                video_id=video_id,
                language=lang,
                error=str(exc),
            )


def _fetch_transcript_once(
    *,
    video_id: str,
    languages: List[str],
    lang: str,
    prefer_auto: bool,
    include_timestamps: bool,
    kind: str,
) -> Dict[str, Any]:
    transcripts = YouTubeTranscriptApi().list(video_id)

    transcript_obj = None
    is_auto_generated = None

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

    if not transcript_obj:
        raise NoTranscriptFound()

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

    segments_payload = None
    if include_timestamps:
        segments_payload = [
            {
                "start": _seg_start(seg),
                "duration": _seg_duration(seg),
                "text": _seg_text(seg),
            }
            for seg in segments
        ]

    metadata = get_metadata(video_id)
    cache_entry = set_cache_entry(
        video_id,
        lang,
        kind,
        transcript_text,
        segments=segments_payload,
        metadata=metadata,
        is_auto_generated=bool(is_auto_generated),
        fetch_method=FETCH_POLICY.fetch_method,
        cache_path=CACHE_PATH,
    )
    logger.info(
        "cache store video_id=%s lang=%s kind=%s method=%s",
        video_id,
        lang,
        kind,
        FETCH_POLICY.fetch_method,
    )

    result: Dict[str, Any] = {
        "video_id": video_id,
        "language": transcript_obj.language or lang,
        "is_auto_generated": bool(is_auto_generated),
        "transcript_text": transcript_text,
        "cache": _format_cache_meta(cache_entry),
        "source": "network",
    }

    if include_timestamps and segments_payload is not None:
        result["segments"] = segments_payload
    if metadata:
        result["metadata"] = metadata

    return result


def main() -> None:
    logger.info("youtube_transcriber server ready")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

