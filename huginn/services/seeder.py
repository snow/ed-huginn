"""Database seeding service - imports Spansh galaxy data."""

import gzip
from pathlib import Path

import ijson
import psycopg
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

SPANSH_URL = "https://downloads.spansh.co.uk/galaxy_populated.json.gz"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DUMP_FILE = DATA_DIR / "galaxy_populated.json.gz"


# Batch size for inserts
BATCH_SIZE = 1000

# Expected system count (for progress bar)
EXPECTED_SYSTEMS = 110000

# Database connection
DB_URL = "postgresql://huginn:huginn@localhost:5432/huginn"

console = Console()


def _get_remote_size() -> int | None:
    """Get the file size in bytes from Spansh server."""
    try:
        response = requests.head(SPANSH_URL, timeout=10)
        response.raise_for_status()
        return int(response.headers.get("content-length", 0)) or None
    except requests.RequestException:
        return None


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


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


def seed_database() -> bool:
    """Seed the database from Spansh dump file.

    Returns True if seeding was successful or already done.
    """
    if is_db_seeded():
        count = get_system_count()
        console.print(f"[green]Database already seeded.[/green] ({count:,} systems)")
        return True

    # Check if dump file exists
    if not DUMP_FILE.exists():
        console.print("[yellow]Please download galaxy_populated.json.gz from https://spansh.co.uk/dumps[/yellow]")
        console.print(f"[dim]Save to:[/dim] {DUMP_FILE}")
        return False

    # Verify file size matches remote
    local_size = DUMP_FILE.stat().st_size
    remote_size = _get_remote_size()

    if remote_size is None:
        console.print("[yellow]Warning:[/yellow] Could not verify file size (server unreachable)")
        console.print(f"[dim]Local file:[/dim] {_format_size(local_size)}")
        console.print()
    elif local_size != remote_size:
        console.print(f"[red]File size mismatch![/red] Local: {_format_size(local_size)}, Remote: {_format_size(remote_size)}")
        console.print("[dim]File may be corrupted or incomplete. Please re-download it.[/dim]")
        return False
    else:
        console.print(f"[green]File verified:[/green] {_format_size(local_size)}")

    # Import data
    console.print()
    console.print("[cyan]Importing systems from Spansh dump...[/cyan]")
    console.print("[dim]This streams the 21GB file - memory usage stays low.[/dim]")
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

                for system in _stream_systems(DUMP_FILE):
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
