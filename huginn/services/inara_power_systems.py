"""INARA scraper service - fetches powerplay data."""

from datetime import datetime, timezone

import psycopg
import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from huginn.config import get_pledged_power, get_power_url

from huginn.services.utils import DB_URL, USER_AGENT, clean_system_name

console = Console()


def _fetch_page(url: str) -> str | None:
    """Fetch a page from INARA."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]Failed to fetch {url}:[/red] {e}")
        return None


def _parse_systems_page(html: str) -> list[dict]:
    """Parse a systems table from INARA HTML (works for contested/controlled).

    Returns list of dicts with: name, state, inara_updated_at (as datetime)
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    # Find which column has "Updated" header
    headers = table.find_all("th")
    updated_col = -1  # Default to last column
    for i, th in enumerate(headers):
        if "Updated" in th.get_text():
            updated_col = i
            break

    systems = []
    rows = table.find_all("tr")

    # Skip header row
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:  # Need at least name, state, updated
            continue

        name = clean_system_name(cells[0].get_text(strip=True))
        state = cells[1].get_text(strip=True)

        # Get updated timestamp from the correct column
        updated_cell = cells[updated_col] if updated_col >= 0 else cells[-1]
        updated_ts = updated_cell.get("data-order")

        if not name or not updated_ts:
            continue

        try:
            inara_updated_at = datetime.fromtimestamp(
                int(updated_ts), tz=timezone.utc
            )
        except (ValueError, TypeError):
            continue

        systems.append({
            "name": name,
            "state": state,
            "inara_updated_at": inara_updated_at,
        })

    return systems


def _update_systems(
    conn,
    systems: list[dict],
    power: str,
    label: str,
    candidate_rule: str = "no_change",
) -> tuple[int, int, int]:
    """Update systems in database from INARA data.

    Args:
        conn: Database connection
        systems: List of system dicts from _parse_systems_page
        power: Power name to set
        label: Label for progress bar
        candidate_rule: How to handle is_candidate:
            - "no_change": don't modify is_candidate
            - "always_false": set is_candidate = FALSE
            - "true_if_contested": set TRUE only if state is "Contested"

    Returns:
        Tuple of (updated, skipped, not_found) counts
    """
    updated = 0
    not_found = 0
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(label, total=len(systems))

        for system in systems:
            # Find system in DB by name
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id64, inara_updated_at FROM systems WHERE name = %s",
                    (system["name"],),
                )
                row = cur.fetchone()

            if not row:
                not_found += 1
                progress.update(task, advance=1)
                continue

            db_id64, db_inara_updated = row

            # Check if INARA data is newer
            if db_inara_updated is not None:
                # Make db timestamp timezone-aware for comparison
                if db_inara_updated.tzinfo is None:
                    db_inara_updated = db_inara_updated.replace(tzinfo=timezone.utc)
                if system["inara_updated_at"] <= db_inara_updated:
                    skipped += 1
                    progress.update(task, advance=1)
                    continue

            # Determine is_candidate value
            if candidate_rule == "always_false":
                is_candidate_sql = ", is_candidate = FALSE"
            elif candidate_rule == "true_if_contested" and system["state"] == "Contested":
                is_candidate_sql = ", is_candidate = TRUE"
            else:
                is_candidate_sql = ""

            # Update the system
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE systems
                    SET power = %s,
                        power_state = %s,
                        inara_updated_at = %s{is_candidate_sql},
                        updated_at = NOW()
                    WHERE id64 = %s
                    """,
                    (power, system["state"], system["inara_updated_at"], db_id64),
                )
            updated += 1
            progress.update(task, advance=1)

    return updated, skipped, not_found


def update_from_inara() -> bool:
    """Fetch powerplay systems from INARA and update database.

    Fetches three pages: contested, controlled, exploited.
    - Contested: set is_candidate=true only if state is "Contested"
    - Controlled/Exploited: set is_candidate=false (not good for bounty hunting)

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    # Pages to fetch: (page_type, label, candidate_rule)
    pages = [
        ("contested", "Contested", "true_if_contested"),
        ("controlled", "Controlled", "always_false"),
        ("exploited", "Exploited", "always_false"),
    ]

    try:
        with psycopg.connect(DB_URL) as conn:
            for page_type, label, candidate_rule in pages:
                url = get_power_url(power, page_type)
                console.print(f"[cyan]Fetching {label.lower()} systems for {power}...[/cyan]")
                console.print(f"[dim]{url}[/dim]")

                html = _fetch_page(url)
                if not html:
                    return False

                systems = _parse_systems_page(html)
                console.print(f"[dim]Found {len(systems)} {label.lower()} systems[/dim]")
                console.print()

                if systems:
                    updated, skipped, not_found = _update_systems(
                        conn, systems, power, f"{label}...", candidate_rule
                    )
                    conn.commit()
                    console.print(f"  Updated: {updated}, Skipped: {skipped}, Not in DB: {not_found}")
                    console.print()

            console.print("[green]Done![/green]")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
