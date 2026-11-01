"""memory_store.py — SQLite-Backed Multi-Agent Experience Memory.

Remembers:
  - Bug and vulnerability signatures
  - Patches that worked vs. patches that failed
  - Runtime speedup gains
  - Allows PlannerAgent to retrieve top-k similar historical fixes.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_memory.db")


def get_connection(db_path: str = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db(db_path: str = DB_FILE) -> None:
    """Initialize memory database schema with indexes for rapid signature matching."""
    conn = get_connection(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experience_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL,
                flaw_signature TEXT NOT NULL,
                flaw_summary TEXT NOT NULL,
                original_code TEXT NOT NULL,
                patch_code TEXT NOT NULL,
                success INTEGER NOT NULL,
                runtime_gain_pct REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lang_sig 
            ON experience_records (language, flaw_signature, success);
        """)
        conn.commit()
    finally:
        conn.close()


def normalize_signature(flaw_list: list[str]) -> str:
    """Normalize a list of flaw names into a deterministic sorted signature."""
    cleaned = [f.strip().lower() for f in flaw_list if f and f.strip()]
    return ";".join(sorted(set(cleaned)))


def record_experience(
    language: str,
    flaws: list[str] | str,
    flaw_summary: str,
    original_code: str,
    patch_code: str,
    success: bool,
    runtime_gain_pct: float = 0.0,
    db_path: str = DB_FILE,
) -> int:
    """Store an optimization/hardening outcome in the persistent memory store."""
    init_memory_db(db_path)
    sig = normalize_signature(flaws) if isinstance(flaws, list) else flaws.strip().lower()

    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO experience_records 
            (language, flaw_signature, flaw_summary, original_code, patch_code, success, runtime_gain_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                language.lower(),
                sig,
                flaw_summary,
                original_code,
                patch_code,
                1 if success else 0,
                runtime_gain_pct,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def retrieve_similar_experiences(
    language: str,
    flaws: list[str] | str,
    limit: int = 3,
    success_only: bool = True,
    db_path: str = DB_FILE,
) -> list[dict[str, Any]]:
    """Retrieve top-N historical experiences matching the current flaw signature or language."""
    init_memory_db(db_path)
    sig = normalize_signature(flaws) if isinstance(flaws, list) else flaws.strip().lower()
    lang = language.lower()

    results: list[dict[str, Any]] = []

    conn = get_connection(db_path)
    try:
        # 1. Exact signature match
        success_filter = "AND success = 1" if success_only else ""
        rows = conn.execute(
            f"""
            SELECT * FROM experience_records
            WHERE language = ? AND flaw_signature = ? {success_filter}
            ORDER BY runtime_gain_pct DESC, id DESC
            LIMIT ?
            """,
            (lang, sig, limit),
        ).fetchall()

        for r in rows:
            results.append(dict(r))

        # 2. If not enough, find partial signature matches
        if len(results) < limit and sig:
            first_flaw = sig.split(";")[0]
            exclude_ids = tuple(r["id"] for r in results) if results else (-1,)
            placeholders = ",".join("?" for _ in exclude_ids)
            sub_rows = conn.execute(
                f"""
                SELECT * FROM experience_records
                WHERE language = ? AND flaw_signature LIKE ? AND id NOT IN ({placeholders}) {success_filter}
                ORDER BY runtime_gain_pct DESC, id DESC
                LIMIT ?
                """,
                (lang, f"%{first_flaw}%", *exclude_ids, limit - len(results)),
            ).fetchall()
            for sr in sub_rows:
                results.append(dict(sr))
    finally:
        conn.close()

    return results
