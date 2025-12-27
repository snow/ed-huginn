"""Siriuscorp scraper service - fetches RES data for candidate systems."""

import time
from datetime import timezone

import psycopg
import requests
from bs4 import BeautifulSoup
from rich.console import Console

from huginn.config import SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY
from huginn.services.utils import (
    DB_URL,
    USER_AGENT,
    QUERY_DELAY_SECONDS,
    clean_system_name,
    fetch_latest_tick,
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
        console.print(f"[red]Siriuscorp failed for {system_name}:[/red] {e}")
        return None


def _parse_siriuscorp_results(html: str) -> list[dict]:
    """Parse Siriuscorp bounty hunting results.

    Columns: System, Distance, CNB, Haz RES, High, Med, Low, Owner, Power, Updated
    Returns list of dicts with: name, has_cnb, has_haz_res, has_high_res, has_med_res, has_low_res, updated_at.
    """
    from datetime import datetime, timezone

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
        # Columns: 0=System, 1=Distance, 2=CNB, 3=Haz RES, 4=High, 5=Med, 6=Low, 7=Owner, 8=Power, 9=Updated
        has_cnb = bool(cells[2].get_text(strip=True))
        has_haz = bool(cells[3].get_text(strip=True))
        has_high = bool(cells[4].get_text(strip=True))
        has_med = bool(cells[5].get_text(strip=True))
        has_low = bool(cells[6].get_text(strip=True))

        # Parse timestamp from title attribute (e.g., "2025-12-27T05:50:04+00:00")
        updated_at = None
        updated_cell = cells[-1]  # Last column is Updated
        title = updated_cell.get("title")
        if title:
            try:
                updated_at = datetime.fromisoformat(title)
            except ValueError:
                pass

        if name:
            systems.append({
                "name": name,
                "has_cnb": has_cnb,
                "has_haz_res": has_haz,
                "has_high_res": has_high,
                "has_med_res": has_med,
                "has_low_res": has_low,
                "updated_at": updated_at,
            })

    return systems


def update_res_from_siriuscorp() -> bool:
    """Query Siriuscorp for RES data on all Expansion candidates.

    Updates has_cnb, has_haz_res, has_high_res, has_med_res, has_low_res for candidate systems.
    Skips systems with res_info_updated_at newer than the latest BGS tick.

    Returns True if successful.
    """
    console.print("[cyan]Querying Siriuscorp for candidate RES data...[/cyan]")
    console.print(f"[dim]Query radius: {SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY} ly[/dim]")
    console.print(f"[dim]Delay between queries: {QUERY_DELAY_SECONDS}s[/dim]")
    console.print()

    # Fetch latest BGS tick to check data freshness
    latest_tick = fetch_latest_tick()
    if latest_tick:
        console.print(f"[dim]Latest BGS tick: {latest_tick.strftime('%Y-%m-%d %H:%M')} UTC[/dim]")
    else:
        console.print("[yellow]Could not fetch BGS tick, will refresh all RES data[/yellow]")
    console.print()

    try:
        with psycopg.connect(DB_URL) as conn:
            # Get all Expansion candidates with their RES info timestamps
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, has_cnb, has_haz_res, has_high_res, has_med_res, has_low_res,
                           res_info_updated_at
                    FROM systems
                    WHERE power_state = 'Expansion' AND is_candidate = TRUE
                """)
                candidates = cur.fetchall()

            if not candidates:
                console.print("[yellow]No candidates to query.[/yellow]")
                console.print("[dim]Run 'Update candidates' first.[/dim]")
                return True

            console.print(f"[dim]Found {len(candidates)} candidates[/dim]")
            console.print()

            siriuscorp_updates = 0
            skipped = 0
            queried = 0
            for i, (cand_name, old_cnb, old_haz, old_high, old_med, old_low, res_updated_at) in enumerate(candidates):
                # Check if RES info is already fresh (updated after last tick)
                if latest_tick and res_updated_at:
                    ts = res_updated_at.replace(tzinfo=timezone.utc) if res_updated_at.tzinfo is None else res_updated_at
                    if ts > latest_tick:
                        skipped += 1
                        continue

                if queried > 0:
                    time.sleep(QUERY_DELAY_SECONDS)
                queried += 1

                console.print(
                    f"[cyan]({queried}/{len(candidates) - skipped})[/cyan] {cand_name}..."
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
                new_cnb = cand_res["has_cnb"] and not old_cnb
                new_haz = cand_res["has_haz_res"] and not old_haz
                new_high = cand_res["has_high_res"] and not old_high
                new_med = cand_res["has_med_res"] and not old_med
                new_low = cand_res["has_low_res"] and not old_low

                if new_cnb or new_haz or new_high or new_med or new_low:
                    set_parts = ["updated_at = NOW()"]
                    params = []

                    # Use timestamp from Siriuscorp if available
                    if cand_res.get("updated_at"):
                        set_parts.append("res_info_updated_at = %s")
                        params.append(cand_res["updated_at"])
                    else:
                        set_parts.append("res_info_updated_at = NOW()")

                    if cand_res["has_cnb"]:
                        set_parts.append("has_cnb = TRUE")
                    if cand_res["has_haz_res"]:
                        set_parts.append("has_haz_res = TRUE")
                    if cand_res["has_high_res"]:
                        set_parts.append("has_high_res = TRUE")
                    if cand_res["has_med_res"]:
                        set_parts.append("has_med_res = TRUE")
                    if cand_res["has_low_res"]:
                        set_parts.append("has_low_res = TRUE")

                    params.append(cand_name)
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            UPDATE systems
                            SET {", ".join(set_parts)}
                            WHERE name = %s
                            """,
                            params,
                        )
                    conn.commit()

                    # Show what RES info was added
                    res_parts = []
                    if new_cnb:
                        res_parts.append("C")
                    if new_haz:
                        res_parts.append("H")
                    if new_high:
                        res_parts.append("H")
                    if new_med:
                        res_parts.append("M")
                    if new_low:
                        res_parts.append("L")
                    console.print(f"  [green]+{''.join(res_parts)}[/green]")
                    siriuscorp_updates += 1

            console.print()
            if skipped > 0:
                console.print(f"[dim]Skipped: {skipped} (fresh)[/dim]")
            console.print(f"Queried: {queried}, Updated: {siriuscorp_updates}")
            console.print("[green]Done![/green]")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
