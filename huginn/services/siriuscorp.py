"""Siriuscorp service - finds RES sites via siriuscorp.cc."""

import re
import time
from datetime import datetime, timezone

import psycopg
import requests
from bs4 import BeautifulSoup
from rich.console import Console

from huginn.config import get_pledged_power, SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY
from huginn.services.utils import (
    DB_URL,
    USER_AGENT,
    QUERY_DELAY_SECONDS,
    find_reference_systems,
)

SIRIUSCORP_URL = "https://siriuscorp.cc/bounty/"

console = Console()


def _fetch_siriuscorp(system_name: str, radius_ly: float) -> str | None:
    """Fetch bounty hunting data from Siriuscorp for a reference system."""
    try:
        response = requests.get(
            SIRIUSCORP_URL,
            params={"system": system_name, "radius": int(radius_ly)},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]Failed to fetch {system_name}:[/red] {e}")
        return None


def _parse_siriuscorp_results(html: str) -> list[dict]:
    """Parse Siriuscorp bounty hunting results.

    Returns list of dicts with: name, has_high_res, has_med_res, has_low_res, updated_at
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    systems = []
    rows = table.find_all("tr")

    # Skip header row
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        # Column 0: System name (strip trailing non-ASCII chars like 📄 icon)
        raw_name = cells[0].get_text(strip=True)
        name = re.sub(r"[^\x00-\x7F]+$", "", raw_name).strip()
        # Column 4: High RES (non-empty = has RES)
        has_high = bool(cells[4].get_text(strip=True))
        # Column 5: Med RES
        has_med = bool(cells[5].get_text(strip=True))
        # Column 6: Low RES
        has_low = bool(cells[6].get_text(strip=True))
        # Column 9: Updated - title attribute has ISO timestamp
        updated_at = None
        title = cells[9].get("title", "")
        if title:
            try:
                updated_at = datetime.fromisoformat(title)
            except ValueError:
                pass

        if name:
            systems.append({
                "name": name,
                "has_high_res": has_high,
                "has_med_res": has_med,
                "has_low_res": has_low,
                "updated_at": updated_at,
            })

    return systems


def _update_res_data(conn, systems: list[dict]) -> tuple[int, int]:
    """Update RES data in database.

    Returns (updated, not_found) counts.
    """
    updated = 0
    not_found = 0

    for system in systems:
        # Use timestamp from Siriuscorp, fall back to now if not available
        siriuscorp_ts = system.get("updated_at") or datetime.now(timezone.utc)

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE systems
                SET has_high_res = %s,
                    has_med_res = %s,
                    has_low_res = %s,
                    siriuscorp_updated_at = %s,
                    updated_at = NOW()
                WHERE name = %s
                RETURNING id64
                """,
                (
                    system["has_high_res"],
                    system["has_med_res"],
                    system["has_low_res"],
                    siriuscorp_ts,
                    system["name"],
                ),
            )
            if cur.fetchone():
                updated += 1
            else:
                not_found += 1

    return updated, not_found


def update_from_siriuscorp() -> bool:
    """Query Siriuscorp for RES data and update database.

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    console.print(f"[cyan]Updating RES data from Siriuscorp for {power}...[/cyan]")
    console.print(f"[dim]Query radius: {SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY} ly[/dim]")
    console.print(f"[dim]Delay between queries: {QUERY_DELAY_SECONDS}s[/dim]")
    console.print()

    try:
        with psycopg.connect(DB_URL) as conn:
            # Count target systems
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM systems
                    WHERE power_state = 'Expansion' AND has_ring = TRUE
                """)
                total_targets = cur.fetchone()[0]

            if total_targets == 0:
                console.print("[yellow]No Expansion systems with rings found.[/yellow]")
                console.print("[dim]Run 'Update INARA data' first.[/dim]")
                return False

            console.print(f"[dim]Found {total_targets} target systems to cover[/dim]")
            console.print()

            # Calculate reference systems
            console.print("[cyan]Calculating minimum reference systems...[/cyan]")
            reference_systems = find_reference_systems(conn, SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY)
            console.print(f"[green]Need {len(reference_systems)} queries[/green]")
            console.print()

            # Query each reference system
            total_updated = 0
            total_not_found = 0

            for i, ref in enumerate(reference_systems):
                # Delay between queries (except first)
                if i > 0:
                    console.print(f"[dim]Waiting {QUERY_DELAY_SECONDS}s...[/dim]")
                    time.sleep(QUERY_DELAY_SECONDS)

                console.print(
                    f"[cyan]({i+1}/{len(reference_systems)})[/cyan] "
                    f"Querying {ref['name']}..."
                )

                html = _fetch_siriuscorp(ref["name"], SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY)
                if not html:
                    continue

                systems = _parse_siriuscorp_results(html)
                console.print(f"  [dim]Found {len(systems)} systems in response[/dim]")

                if systems:
                    updated, not_found = _update_res_data(conn, systems)
                    conn.commit()
                    total_updated += updated
                    total_not_found += not_found
                    console.print(f"  [dim]Updated: {updated}, Not in DB: {not_found}[/dim]")

            console.print()
            console.print("[green]Done![/green]")
            console.print(f"  Total updated: {total_updated}")
            console.print(f"  Not in DB: {total_not_found}")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False


def plan_siriuscorp_queries() -> bool:
    """Plan which systems to query from Siriuscorp.

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    console.print(f"[cyan]Planning Siriuscorp queries for {power}...[/cyan]")
    console.print(f"[dim]Query radius: {SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY} ly[/dim]")
    console.print()

    try:
        with psycopg.connect(DB_URL) as conn:
            # Count Expansion systems with rings
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM systems
                    WHERE power_state = 'Expansion' AND has_ring = TRUE
                """)
                total_expansion = cur.fetchone()[0]

            if total_expansion == 0:
                console.print("[yellow]No Expansion systems with rings found.[/yellow]")
                console.print("[dim]Run 'Update INARA data' first.[/dim]")
                return False

            console.print(f"[dim]Found {total_expansion} Expansion systems with rings to cover[/dim]")
            console.print()
            console.print("[cyan]Calculating minimum reference systems...[/cyan]")
            console.print("[dim]This may take a moment...[/dim]")
            console.print()

            reference_systems = find_reference_systems(conn, SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY)

            console.print(f"[green]Found {len(reference_systems)} reference systems[/green]")
            console.print(f"[dim]Coverage efficiency: {total_expansion / len(reference_systems):.1f} systems per query[/dim]")
            console.print()

            console.print("[bold]Reference systems:[/bold]")
            for i, ref in enumerate(reference_systems, 1):
                console.print(
                    f"  {i:3}. {ref['name']:<30} "
                    f"covers {ref['covers']:3} systems  "
                    f"[dim]({ref['x']:.1f}, {ref['y']:.1f}, {ref['z']:.1f})[/dim]"
                )

            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
