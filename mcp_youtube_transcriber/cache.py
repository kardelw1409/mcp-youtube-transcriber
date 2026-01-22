import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


DEFAULT_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), "transcripts_cache.sqlite3"
)


@dataclass
class CacheEntry:
    transcript_text: str
    segments: Optional[list]
    sha256: str
    fetched_at: float
    fetch_method: str
    metadata: Optional[Dict[str, Any]]
    is_auto_generated: Optional[bool]


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def init_cache_db(path: str = DEFAULT_CACHE_PATH) -> None:
    _ensure_parent_dir(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
                video_id TEXT NOT NULL,
                language TEXT NOT NULL,
                kind TEXT NOT NULL,
                transcript_text TEXT NOT NULL,
                segments_json TEXT,
                metadata_json TEXT,
                is_auto_generated INTEGER,
                sha256 TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                fetch_method TEXT NOT NULL,
                PRIMARY KEY (video_id, language, kind)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transcripts_fetched_at "
            "ON transcripts(fetched_at)"
        )
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(transcripts)")
        }
        if "segments_json" not in existing_columns:
            conn.execute("ALTER TABLE transcripts ADD COLUMN segments_json TEXT")
        if "metadata_json" not in existing_columns:
            conn.execute("ALTER TABLE transcripts ADD COLUMN metadata_json TEXT")
        if "is_auto_generated" not in existing_columns:
            conn.execute("ALTER TABLE transcripts ADD COLUMN is_auto_generated INTEGER")


def compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_cache_entry(
    video_id: str,
    language: str,
    kind: str,
    *,
    cache_path: str = DEFAULT_CACHE_PATH,
) -> Optional[CacheEntry]:
    init_cache_db(cache_path)
    with sqlite3.connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT transcript_text, segments_json, metadata_json, is_auto_generated,
                   sha256, fetched_at, fetch_method
            FROM transcripts
            WHERE video_id = ? AND language = ? AND kind = ?
            """,
            (video_id, language, kind),
        ).fetchone()

    if not row:
        return None

    (
        transcript_text,
        segments_json,
        metadata_json,
        is_auto_generated,
        sha256,
        fetched_at,
        fetch_method,
    ) = row
    segments = json.loads(segments_json) if segments_json else None
    metadata = json.loads(metadata_json) if metadata_json else None
    return CacheEntry(
        transcript_text=transcript_text,
        segments=segments,
        sha256=sha256,
        fetched_at=float(fetched_at),
        fetch_method=fetch_method,
        metadata=metadata,
        is_auto_generated=(
            bool(is_auto_generated) if is_auto_generated is not None else None
        ),
    )


def set_cache_entry(
    video_id: str,
    language: str,
    kind: str,
    transcript_text: str,
    *,
    segments: Optional[list],
    metadata: Optional[Dict[str, Any]],
    is_auto_generated: Optional[bool],
    fetch_method: str,
    cache_path: str = DEFAULT_CACHE_PATH,
) -> CacheEntry:
    init_cache_db(cache_path)
    sha256 = compute_sha256(transcript_text)
    fetched_at = time.time()
    segments_json = json.dumps(segments) if segments is not None else None
    metadata_json = json.dumps(metadata) if metadata is not None else None

    with sqlite3.connect(cache_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO transcripts
                (video_id, language, kind, transcript_text, segments_json,
                 metadata_json, is_auto_generated, sha256, fetched_at, fetch_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                language,
                kind,
                transcript_text,
                segments_json,
                metadata_json,
                None if is_auto_generated is None else int(is_auto_generated),
                sha256,
                fetched_at,
                fetch_method,
            ),
        )

    return CacheEntry(
        transcript_text=transcript_text,
        segments=segments,
        sha256=sha256,
        fetched_at=fetched_at,
        fetch_method=fetch_method,
        metadata=metadata,
        is_auto_generated=is_auto_generated,
    )
