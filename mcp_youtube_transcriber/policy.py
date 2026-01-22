import math
from dataclasses import dataclass
from typing import List


@dataclass
class FetchClassification:
    fetch_method: str
    libraries_detected: List[str]
    auth_required: bool


@dataclass
class FetchPolicy:
    fetch_method: str
    auth_required: bool
    ttl_seconds: int
    min_interval_seconds: float
    jitter_seconds: float
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    cooldown_seconds: int
    safe_max_per_hour: int


def detect_fetch_method() -> FetchClassification:
    libraries: List[str] = []
    method = "SCRAPE"
    auth_required = False

    try:
        import googleapiclient  # type: ignore

        libraries.append("googleapiclient")
        method = "YT_DATA_API"
        auth_required = True
    except Exception:
        pass

    try:
        import youtube_transcript_api  # type: ignore

        libraries.append("youtube_transcript_api")
        if method != "YT_DATA_API":
            method = "YT_TRANSCRIPT_API"
    except Exception:
        pass

    try:
        import yt_dlp  # type: ignore

        libraries.append("yt_dlp")
    except Exception:
        pass

    return FetchClassification(
        fetch_method=method,
        libraries_detected=libraries,
        auth_required=auth_required,
    )


def policy_for(classification: FetchClassification) -> FetchPolicy:
    method = classification.fetch_method
    if method == "YT_DATA_API":
        return FetchPolicy(
            fetch_method=method,
            auth_required=True,
            ttl_seconds=14 * 24 * 60 * 60,
            min_interval_seconds=1.0,
            jitter_seconds=0.5,
            max_retries=2,
            backoff_base_seconds=2.0,
            backoff_max_seconds=30.0,
            cooldown_seconds=5 * 60,
            safe_max_per_hour=50,
        )
    if method == "YT_TRANSCRIPT_API":
        return FetchPolicy(
            fetch_method=method,
            auth_required=False,
            ttl_seconds=60 * 24 * 60 * 60,
            min_interval_seconds=8.0,
            jitter_seconds=2.0,
            max_retries=3,
            backoff_base_seconds=2.0,
            backoff_max_seconds=60.0,
            cooldown_seconds=15 * 60,
            safe_max_per_hour=5,
        )
    return FetchPolicy(
        fetch_method=method,
        auth_required=False,
        ttl_seconds=int(math.pow(10, 9)),
        min_interval_seconds=12.0,
        jitter_seconds=3.0,
        max_retries=1,
        backoff_base_seconds=2.0,
        backoff_max_seconds=20.0,
        cooldown_seconds=30 * 60,
        safe_max_per_hour=3,
    )

