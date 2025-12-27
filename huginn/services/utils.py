"""Shared utilities for Huginn services."""

import os

import numpy as np
import psycopg

from huginn.config import CANDIDACY_QUERY_RADIUS_LY

DB_URL = os.environ.get("DATABASE_URL", "postgresql://huginn:huginn@localhost:5432/huginn")


def is_db_seeded() -> bool:
    """Check if the database has been seeded."""
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM systems")
                count = cur.fetchone()[0]
                return count > 0
    except Exception:
        return False


def get_system_count() -> int:
    """Get current system count in database."""
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM systems")
                return cur.fetchone()[0]
    except Exception:
        return 0


USER_AGENT = "Huginn/1.0 (Elite Dangerous Personal Analysis Tool; https://github.com/snow/ed-huginn)"
QUERY_DELAY_SECONDS = 10


def clean_system_name(name: str) -> str:
    """Sanitize system name by stripping non-alphanumeric leading/trailing chars.

    Handles INARA's unicode decorations (U+E81D, U+FE0E) and other edge cases.
    """
    import re
    return re.sub(r'^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$', '', name)


def find_reference_systems(conn, radius_ly: float = CANDIDACY_QUERY_RADIUS_LY) -> list[dict]:
    """Find minimum reference systems to cover all Expansion systems with rings.

    Uses greedy Set Cover algorithm with numpy for fast distance calculations:
    1. Fetch all systems in one query
    2. Precompute pairwise distances with numpy
    3. Greedily select systems that cover the most uncovered systems

    Args:
        conn: Database connection
        radius_ly: Query radius in light-years

    Returns:
        List of reference systems with coverage info
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id64, name, x, y, z
            FROM systems
            WHERE power_state = 'Expansion' AND has_ring = TRUE
        """)
        rows = cur.fetchall()

    if not rows:
        return []

    # Build lookup structures
    n = len(rows)
    ids = [row[0] for row in rows]
    names = [row[1] for row in rows]
    coords = np.array([[row[2], row[3], row[4]] for row in rows], dtype=np.float64)

    # Precompute pairwise distances using broadcasting
    # For n=500 systems, this is ~2MB of memory and runs in milliseconds
    diff = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]
    distances = np.sqrt(np.sum(diff ** 2, axis=2))

    # Precompute coverage masks: coverage[i] = set of indices within radius of i
    coverage = [set(np.where(distances[i] <= radius_ly)[0]) for i in range(n)]

    # Greedy set cover
    uncovered = set(range(n))
    reference_indices = []

    while uncovered:
        best_idx = None
        best_count = 0
        best_covers = set()

        for idx in uncovered:
            # Intersect precomputed coverage with current uncovered set
            covers = coverage[idx] & uncovered
            if len(covers) > best_count:
                best_idx = idx
                best_count = len(covers)
                best_covers = covers

        if best_idx is None:
            break

        reference_indices.append((best_idx, len(best_covers)))
        uncovered -= best_covers

    # Build result list
    return [
        {
            "id64": ids[idx],
            "name": names[idx],
            "x": float(coords[idx, 0]),
            "y": float(coords[idx, 1]),
            "z": float(coords[idx, 2]),
            "covers": covers_count,
        }
        for idx, covers_count in reference_indices
    ]


def mark_candidates(conn, target_systems: set[str]) -> int:
    """Mark systems as candidates if they are Expansion+has_ring and appeared as targets.

    Returns count of systems marked as candidates.
    """
    if not target_systems:
        return 0

    marked = 0
    with conn.cursor() as cur:
        for name in target_systems:
            cur.execute(
                """
                UPDATE systems
                SET is_candidate = TRUE, updated_at = NOW()
                WHERE name = %s
                  AND power_state = 'Expansion'
                  AND has_ring = TRUE
                  AND (is_candidate IS NULL OR is_candidate = FALSE)
                RETURNING id64
                """,
                (name,),
            )
            if cur.fetchone():
                marked += 1

    return marked
