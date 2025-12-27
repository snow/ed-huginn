"""Siriuscorp scraper service - fetches RES data for candidate systems."""

import time

import psycopg
import requests
from bs4 import BeautifulSoup
from rich.console import Console

from huginn.config import SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY
from huginn.services.utils import DB_URL, USER_AGENT, QUERY_DELAY_SECONDS, clean_system_name

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
        console.print(f"[red]Siriuscorp failed for {system_name}:[/red] {e}")
        return None


def _parse_siriuscorp_results(html: str) -> list[dict]:
    """Parse Siriuscorp bounty hunting results.

    Returns list of dicts with: name, has_high_res, has_med_res, has_low_res.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    systems = []
    rows = table.find_all("tr")

    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        raw_name = cells[0].get_text(strip=True)
        name = clean_system_name(raw_name)
        has_high = bool(cells[4].get_text(strip=True))
        has_med = bool(cells[5].get_text(strip=True))
        has_low = bool(cells[6].get_text(strip=True))

        if name:
            systems.append({
                "name": name,
                "has_high_res": has_high,
                "has_med_res": has_med,
                "has_low_res": has_low,
            })

    return systems


def update_res_from_siriuscorp() -> bool:
    """Query Siriuscorp for RES data on all Expansion candidates.

    Updates has_high_res, has_med_res, has_low_res for candidate systems.

    Returns True if successful.
    """
    console.print("[cyan]Querying Siriuscorp for candidate RES data...[/cyan]")
    console.print(f"[dim]Query radius: {SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY} ly[/dim]")
    console.print(f"[dim]Delay between queries: {QUERY_DELAY_SECONDS}s[/dim]")
    console.print()

    try:
        with psycopg.connect(DB_URL) as conn:
            # Get all Expansion candidates
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, has_high_res, has_med_res, has_low_res
                    FROM systems
                    WHERE power_state = 'Expansion' AND is_candidate = TRUE
                """)
                candidates = cur.fetchall()

            if not candidates:
                console.print("[yellow]No candidates to query.[/yellow]")
                console.print("[dim]Run 'Update candidates' first.[/dim]")
                return True

            console.print(f"[dim]Querying {len(candidates)} candidates[/dim]")
            console.print()

            siriuscorp_updates = 0
            for i, (cand_name, old_high, old_med, old_low) in enumerate(candidates):
                if i > 0:
                    time.sleep(QUERY_DELAY_SECONDS)

                console.print(
                    f"[cyan]({i+1}/{len(candidates)})[/cyan] {cand_name}..."
                )

                html = _fetch_siriuscorp(cand_name, SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY)
                if not html:
                    continue

                systems = _parse_siriuscorp_results(html)

                # Find our candidate in the results
                cand_res = None
                for sys in systems:
                    if sys["name"] == cand_name:
                        cand_res = sys
                        break

                if not cand_res:
                    console.print(f"  [dim]Not found in response[/dim]")
                    continue

                # Check if Siriuscorp has new RES info
                new_high = cand_res["has_high_res"] and not old_high
                new_med = cand_res["has_med_res"] and not old_med
                new_low = cand_res["has_low_res"] and not old_low

                if new_high or new_med or new_low:
                    set_parts = ["updated_at = NOW()"]
                    if cand_res["has_high_res"]:
                        set_parts.append("has_high_res = TRUE")
                    if cand_res["has_med_res"]:
                        set_parts.append("has_med_res = TRUE")
                    if cand_res["has_low_res"]:
                        set_parts.append("has_low_res = TRUE")

                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            UPDATE systems
                            SET {", ".join(set_parts)}
                            WHERE name = %s
                            """,
                            (cand_name,),
                        )
                    conn.commit()

                    # Show what RES info was added
                    res_parts = []
                    if new_high:
                        res_parts.append("H")
                    if new_med:
                        res_parts.append("M")
                    if new_low:
                        res_parts.append("L")
                    console.print(f"  [green]+{''.join(res_parts)}[/green]")
                    siriuscorp_updates += 1

            console.print()
            console.print(f"Updated: {siriuscorp_updates}")
            console.print("[green]Done![/green]")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
