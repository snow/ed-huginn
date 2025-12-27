"""Database seeding service - imports Spansh galaxy data."""

import gzip
from pathlib import Path

import ijson
import psycopg
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from huginn.services.utils import DB_URL, get_system_count, is_db_seeded

DATA_DIR = Path(__file__).parent.parent.parent / "data"

# Batch size for inserts
BATCH_SIZE = 1000

# Expected system count (for progress bar)
EXPECTED_SYSTEMS = 110000

console = Console()


def _find_dump_file() -> Path | None:
    """Find the latest galaxy_*.json.gz file in the data directory."""
    matches = list(DATA_DIR.glob("galaxy_*.json.gz"))
    if not matches:
        return None
    # Return the most recently modified file
    return max(matches, key=lambda p: p.stat().st_mtime)


def _has_rings(system: dict) -> bool:
    """Check if any body in the system has rings (potential RES sites)."""
    for body in system.get("bodies", []):
        if body.get("rings"):
            return True
    return False


def _stream_systems(filepath: Path):
    """Stream parse systems from gzip file, yielding one at a time."""
    with gzip.open(filepath, "rb") as f:
        for system in ijson.items(f, "item"):
            coords = system.get("coords", {})
            yield {
                "id64": system["id64"],
                "name": system["name"],
                "x": coords.get("x", 0),
                "y": coords.get("y", 0),
                "z": coords.get("z", 0),
                "has_ring": _has_rings(system),
            }


def _insert_batch(conn, batch: list[dict]) -> None:
    """Insert a batch of systems into the database."""
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO systems (id64, name, x, y, z, has_ring, spansh_updated_at)
            VALUES (%(id64)s, %(name)s, %(x)s, %(y)s, %(z)s, %(has_ring)s, NOW())
            ON CONFLICT (id64) DO UPDATE SET
                name = EXCLUDED.name,
                x = EXCLUDED.x,
                y = EXCLUDED.y,
                z = EXCLUDED.z,
                has_ring = EXCLUDED.has_ring,
                spansh_updated_at = NOW(),
                updated_at = NOW()
            """,
            batch,
        )


def import_from_spansh() -> bool:
    """Import/update systems from Spansh dump file.

    Finds the latest galaxy_*.json.gz file in the data directory.
    Returns True if import was successful.
    """
    current_count = get_system_count()
    if current_count > 0:
        console.print(f"[dim]Current database:[/dim] {current_count:,} systems")

    # Find dump file
    dump_file = _find_dump_file()
    if not dump_file:
        console.print("[yellow]No galaxy_*.json.gz found in data/[/yellow]")
        console.print("[dim]Download from https://spansh.co.uk/dumps[/dim]")
        return False

    # Import data
    console.print()
    console.print(f"[cyan]Importing systems from {dump_file.name}...[/cyan]")
    console.print("[dim]This streams the file - memory usage stays low.[/dim]")
    console.print()

    try:
        with psycopg.connect(DB_URL) as conn:
            batch = []
            total = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[dim]{task.completed:,} systems[/dim]"),
                console=console,
            ) as progress:
                task = progress.add_task("Importing...", total=EXPECTED_SYSTEMS)

                for system in _stream_systems(dump_file):
                    batch.append(system)

                    if len(batch) >= BATCH_SIZE:
                        _insert_batch(conn, batch)
                        conn.commit()
                        total += len(batch)
                        progress.update(task, completed=total)
                        batch = []

                # Insert remaining
                if batch:
                    _insert_batch(conn, batch)
                    conn.commit()
                    total += len(batch)
                    progress.update(task, completed=total)

        console.print()
        console.print(f"[green]Done![/green] Imported {total:,} systems.")
        return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return False
