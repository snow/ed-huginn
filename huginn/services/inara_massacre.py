"""INARA massacre missions scraper - finds massacre mission targets."""

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
)

INARA_MASSACRE_URL = "https://inara.cz/elite/nearest-misc/"

console = Console()


def _fetch_inara_massacre(system_name: str) -> str | None:
    """Fetch massacre mission data from INARA for a reference system."""
    try:
        response = requests.get(
            INARA_MASSACRE_URL,
            params={"ps1": system_name, "pi20": 9},  # pi20=9 means "Nearest massacre missions"
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]Failed to fetch {system_name}:[/red] {e}")
        return None


def _clean_system_name(name: str) -> str:
    """Remove INARA's trailing unicode decorations from system names."""
    # INARA adds U+E81D (Private Use Area) and U+FE0E (Variation Selector)
    return name.rstrip("\ue81d\ufe0e")


def _parse_inara_massacre_results(html: str) -> dict[str, dict]:
    """Parse INARA massacre mission results to extract target system names and RES info.

    Returns dict of {system_name: {"has_high_res": bool, "has_low_res": bool, "has_haz_res": bool}}.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the table - it uses class "tablesortercollapsed"
    # Columns: Source star system, Allegiance, Pad, Target faction, Target star system, Dist
    table = soup.find("table", class_="tablesortercollapsed")
    if not table:
        return {}

    target_systems: dict[str, dict] = {}
    tbody = table.find("tbody")
    if not tbody:
        return {}

    rows = tbody.find_all("tr")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        # Target system is in column 5 (index 4) - "Target star system"
        # Format: <a href="/elite/starsystem/10166/">Eledolyaks</a>
        # RES tags: <span class="tag taginline floatright">High RES</span>
        system_cell = cells[4]
        system_link = system_cell.find("a", href=lambda h: h and "/starsystem/" in h)

        if system_link:
            system_name = _clean_system_name(system_link.get_text(strip=True))
            if not system_name:
                continue

            # Extract RES info from tags
            tags = system_cell.find_all("span", class_="tag")
            tag_texts = [tag.get_text(strip=True).lower() for tag in tags]

            has_high_res = any("high res" in t for t in tag_texts)
            has_low_res = any("low res" in t for t in tag_texts)
            has_haz_res = any("haz res" in t for t in tag_texts)

            # Merge with existing data (if system appears multiple times)
            if system_name in target_systems:
                target_systems[system_name]["has_high_res"] |= has_high_res
                target_systems[system_name]["has_low_res"] |= has_low_res
                target_systems[system_name]["has_haz_res"] |= has_haz_res
            else:
                target_systems[system_name] = {
                    "has_high_res": has_high_res,
                    "has_low_res": has_low_res,
                    "has_haz_res": has_haz_res,
                }

    return target_systems


def _mark_candidates_with_res(conn, target_systems: dict[str, dict]) -> int:
    """Mark systems as candidates and update RES info.

    Args:
        conn: Database connection
        target_systems: Dict of {name: {"has_high_res": bool, "has_low_res": bool, "has_haz_res": bool}}

    Returns count of systems marked as candidates.
    """
    if not target_systems:
        return 0

    marked = 0
    with conn.cursor() as cur:
        for name, res_info in target_systems.items():
            # Build SET clause - only update RES columns if they're True
            set_parts = ["is_candidate = TRUE", "updated_at = NOW()"]
            params = []

            if res_info.get("has_high_res"):
                set_parts.append("has_high_res = TRUE")
            if res_info.get("has_low_res"):
                set_parts.append("has_low_res = TRUE")
            # Note: has_haz_res is stored but user said to leave has_med_res alone

            params.append(name)

            cur.execute(
                f"""
                UPDATE systems
                SET {", ".join(set_parts)}
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


def update_from_inara_massacre() -> bool:
    """Query INARA for massacre mission targets and mark candidates.

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    console.print(f"[cyan]Updating candidate data from INARA massacre missions for {power}...[/cyan]")
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
            all_targets: dict[str, dict] = {}

            for i, ref in enumerate(reference_systems):
                # Delay between queries (except first)
                if i > 0:
                    console.print(f"[dim]Waiting {QUERY_DELAY_SECONDS}s...[/dim]")
                    time.sleep(QUERY_DELAY_SECONDS)

                console.print(
                    f"[cyan]({i+1}/{len(reference_systems)})[/cyan] "
                    f"Querying {ref['name']}..."
                )

                html = _fetch_inara_massacre(ref["name"])
                if not html:
                    continue

                targets = _parse_inara_massacre_results(html)
                console.print(f"  [dim]Found {len(targets)} target systems[/dim]")

                # Merge targets with existing data
                for name, res_info in targets.items():
                    if name in all_targets:
                        all_targets[name]["has_high_res"] |= res_info["has_high_res"]
                        all_targets[name]["has_low_res"] |= res_info["has_low_res"]
                        all_targets[name]["has_haz_res"] |= res_info["has_haz_res"]
                    else:
                        all_targets[name] = res_info.copy()

            console.print()
            console.print(f"[dim]Total unique targets found: {len(all_targets)}[/dim]")

            # Count RES info
            high_res_count = sum(1 for t in all_targets.values() if t["has_high_res"])
            low_res_count = sum(1 for t in all_targets.values() if t["has_low_res"])
            haz_res_count = sum(1 for t in all_targets.values() if t["has_haz_res"])
            console.print(f"[dim]  High RES: {high_res_count}, Low RES: {low_res_count}, Haz RES: {haz_res_count}[/dim]")

            # Mark candidates
            marked = _mark_candidates_with_res(conn, all_targets)
            conn.commit()

            console.print()
            console.print("[green]Done![/green]")
            console.print(f"  New candidates marked: {marked}")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
