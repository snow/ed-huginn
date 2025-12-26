"""EDTools service - finds massacre mission targets via edtools.cc."""

import time

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
    mark_candidates,
)

EDTOOLS_URL = "https://edtools.cc/pve"

console = Console()


def _fetch_edtools(system_name: str, radius_ly: float) -> str | None:
    """Fetch PVE data from EDTools for a reference system."""
    try:
        response = requests.get(
            EDTOOLS_URL,
            params={"s": system_name, "md": int(radius_ly)},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]Failed to fetch {system_name}:[/red] {e}")
        return None


def _parse_edtools_results(html: str) -> set[str]:
    """Parse EDTools PVE results to extract target system names.

    Returns set of target system names found in the results.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="sys_tbl")
    if not table:
        return set()

    target_systems = set()
    rows = table.find_all("tr")

    # Skip header row
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        # Target system is in column 10 (index 9) - "Target/Sources"
        target_cell = cells[9]

        # Find the EDSM link which contains the target system name
        # Format: <a href="https://www.edsm.net/..." class="bl nd">System Name</a>
        edsm_link = target_cell.find("a", href=lambda h: h and "edsm.net" in h)
        if edsm_link:
            system_name = edsm_link.get_text(strip=True)
            if system_name:
                target_systems.add(system_name)

    return target_systems


def update_from_edtools() -> bool:
    """Query EDTools for massacre mission targets and mark candidates.

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    console.print(f"[cyan]Updating candidate data from EDTools for {power}...[/cyan]")
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

            console.print(f"[dim]Found {total_targets} Expansion systems with rings[/dim]")
            console.print()

            # Calculate reference systems
            console.print("[cyan]Calculating minimum reference systems...[/cyan]")
            reference_systems = find_reference_systems(conn, SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY)
            console.print(f"[green]Need {len(reference_systems)} queries[/green]")
            console.print()

            # Query each reference system
            all_targets: set[str] = set()

            for i, ref in enumerate(reference_systems):
                # Delay between queries (except first)
                if i > 0:
                    console.print(f"[dim]Waiting {QUERY_DELAY_SECONDS}s...[/dim]")
                    time.sleep(QUERY_DELAY_SECONDS)

                console.print(
                    f"[cyan]({i+1}/{len(reference_systems)})[/cyan] "
                    f"Querying {ref['name']}..."
                )

                html = _fetch_edtools(ref["name"], SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY)
                if not html:
                    continue

                targets = _parse_edtools_results(html)
                console.print(f"  [dim]Found {len(targets)} target systems[/dim]")
                all_targets.update(targets)

            console.print()
            console.print(f"[dim]Total unique targets found: {len(all_targets)}[/dim]")

            # Mark candidates
            marked = mark_candidates(conn, all_targets)
            conn.commit()

            console.print()
            console.print("[green]Done![/green]")
            console.print(f"  New candidates marked: {marked}")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
