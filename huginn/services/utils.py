"""Shared utilities for Huginn services."""

import psycopg

from huginn.config import CANDIDACY_QUERY_RADIUS_LY

DB_URL = "postgresql://huginn:huginn@localhost:5432/huginn"


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


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
QUERY_DELAY_SECONDS = 3


def clean_system_name(name: str) -> str:
    """Sanitize system name by stripping non-alphanumeric leading/trailing chars.

    Handles INARA's unicode decorations (U+E81D, U+FE0E) and other edge cases.
    """
    import re
    return re.sub(r'^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$', '', name)


def find_reference_systems(conn, radius_ly: float = CANDIDACY_QUERY_RADIUS_LY) -> list[dict]:
    """Find minimum reference systems to cover all Expansion systems with rings.

    Uses greedy Set Cover algorithm:
    1. Find the system that covers the most uncovered systems
    2. Mark all systems within radius as covered
    3. Repeat until all systems are covered

    Args:
        conn: Database connection
        radius_ly: Query radius in light-years

    Returns:
        List of reference systems with coverage info
    """
    with conn.cursor() as cur:
        # Get all Expansion systems with rings (potential targets)
        cur.execute("""
            SELECT id64, name, x, y, z
            FROM systems
            WHERE power_state = 'Expansion' AND has_ring = TRUE
        """)
        expansion_systems = {row[0]: {"name": row[1], "x": row[2], "y": row[3], "z": row[4]}
                            for row in cur.fetchall()}

    if not expansion_systems:
        return []

    uncovered = set(expansion_systems.keys())
    reference_systems = []

    while uncovered:
        best_id = None
        best_covers = set()

        # Find the system that covers the most uncovered systems
        for sys_id in uncovered:
            sys = expansion_systems[sys_id]

            # Find all uncovered systems within radius using SQL
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id64
                    FROM systems
                    WHERE power_state = 'Expansion'
                      AND has_ring = TRUE
                      AND id64 = ANY(%s)
                      AND ST_3DDWithin(
                          coords,
                          ST_MakePoint(%s, %s, %s),
                          %s
                      )
                """, (list(uncovered), sys["x"], sys["y"], sys["z"], radius_ly))
                covers = {row[0] for row in cur.fetchall()}

            if len(covers) > len(best_covers):
                best_id = sys_id
                best_covers = covers

        if best_id is None:
            break

        # Add this system as a reference point
        reference_systems.append({
            "id64": best_id,
            "name": expansion_systems[best_id]["name"],
            "x": expansion_systems[best_id]["x"],
            "y": expansion_systems[best_id]["y"],
            "z": expansion_systems[best_id]["z"],
            "covers": len(best_covers),
        })

        # Remove covered systems
        uncovered -= best_covers

    return reference_systems


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
