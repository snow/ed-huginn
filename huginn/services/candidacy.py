"""Candidacy check service - consolidated scraper for candidate systems."""

import re
import time
from datetime import datetime, timezone

import psycopg
import requests
from bs4 import BeautifulSoup
from rich.console import Console

from huginn.config import (
    get_pledged_power,
    CANDIDACY_QUERY_RADIUS_LY,
    SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY,
)
from huginn.services.utils import (
    DB_URL,
    USER_AGENT,
    QUERY_DELAY_SECONDS,
    find_reference_systems,
)

INARA_MASSACRE_URL = "https://inara.cz/elite/nearest-misc/"
EDTOOLS_URL = "https://edtools.cc/pve"
SIRIUSCORP_URL = "https://siriuscorp.cc/bounty/"

console = Console()


def _fetch_inara_massacre(system_name: str) -> str | None:
    """Fetch massacre mission data from INARA for a reference system."""
    try:
        response = requests.get(
            INARA_MASSACRE_URL,
            params={"ps1": system_name, "pi20": 9},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]INARA failed for {system_name}:[/red] {e}")
        return None


def _fetch_edtools(system_name: str, radius_ly: float) -> str | None:
    """Fetch PVE data from EDTools for a reference system."""
    try:
        response = requests.get(
            EDTOOLS_URL,
            params={"s": system_name, "md": int(radius_ly), "sc": 2},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]EDTools failed for {system_name}:[/red] {e}")
        return None


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


def _clean_system_name(name: str) -> str:
    """Sanitize system name by stripping non-alphanumeric leading/trailing chars."""
    return re.sub(r'^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$', '', name)


def _parse_inara_massacre_results(html: str) -> dict[str, dict]:
    """Parse INARA massacre results.

    Returns dict of {system_name: {"has_high_res": bool, "has_low_res": bool, "has_haz_res": bool}}.
    """
    soup = BeautifulSoup(html, "html.parser")
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

        system_cell = cells[4]
        system_link = system_cell.find("a", href=lambda h: h and "/starsystem/" in h)
        if system_link:
            system_name = _clean_system_name(system_link.get_text(strip=True))
            if not system_name:
                continue

            tags = system_cell.find_all("span", class_="tag")
            tag_texts = [tag.get_text(strip=True).lower() for tag in tags]

            has_high_res = any("high res" in t for t in tag_texts)
            has_low_res = any("low res" in t for t in tag_texts)
            has_haz_res = any("haz res" in t for t in tag_texts)

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


def _parse_edtools_results(html: str) -> dict[str, dict]:
    """Parse EDTools PVE results to extract target system names and RES info.

    Returns dict of {system_name: {"has_high_res": bool, "has_med_res": bool, "has_low_res": bool, "has_haz_res": bool}}.
    RES info is in column 11 (index 10), format: "haz,high,reg,low" or "high,low" or "2 rings" or "no rings".
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="sys_tbl")
    if not table:
        return {}

    target_systems: dict[str, dict] = {}
    rows = table.find_all("tr")

    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 11:
            continue

        # Target system is in column 10 (index 9)
        target_cell = cells[9]
        edsm_link = target_cell.find("a", href=lambda h: h and "edsm.net" in h)
        if not edsm_link:
            continue

        system_name = edsm_link.get_text(strip=True)
        if not system_name:
            continue

        # RES info is in column 11 (index 10)
        # Format: <a href="res?s=...">haz,high,reg,low</a> or just "2 rings" / "no rings"
        res_cell = cells[10]
        res_text = res_cell.get_text(strip=True).lower()

        has_high_res = "high" in res_text
        has_med_res = "reg" in res_text  # "reg" = regular = medium
        has_low_res = "low" in res_text
        has_haz_res = "haz" in res_text

        if system_name in target_systems:
            target_systems[system_name]["has_high_res"] |= has_high_res
            target_systems[system_name]["has_med_res"] |= has_med_res
            target_systems[system_name]["has_low_res"] |= has_low_res
            target_systems[system_name]["has_haz_res"] |= has_haz_res
        else:
            target_systems[system_name] = {
                "has_high_res": has_high_res,
                "has_med_res": has_med_res,
                "has_low_res": has_low_res,
                "has_haz_res": has_haz_res,
            }

    return target_systems


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
        name = _clean_system_name(raw_name)
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


def _reset_non_contest_candidates(conn) -> int:
    """Reset is_candidate = FALSE for all non-Contest systems.

    Returns count of systems reset.
    """
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE systems
            SET is_candidate = FALSE, updated_at = NOW()
            WHERE power_state != 'Contest'
              AND is_candidate = TRUE
            RETURNING id64
        """)
        return cur.rowcount


def _mark_candidates_with_res(conn, target_systems: dict[str, dict]) -> int:
    """Mark systems as candidates and update RES info.

    Args:
        conn: Database connection
        target_systems: Dict of {name: {"has_high_res": bool, "has_med_res": bool, "has_low_res": bool, ...}}

    Returns count of systems marked as candidates.
    """
    if not target_systems:
        return 0

    marked = 0
    with conn.cursor() as cur:
        for name, res_info in target_systems.items():
            set_parts = ["is_candidate = TRUE", "updated_at = NOW()"]

            if res_info.get("has_high_res"):
                set_parts.append("has_high_res = TRUE")
            if res_info.get("has_med_res"):
                set_parts.append("has_med_res = TRUE")
            if res_info.get("has_low_res"):
                set_parts.append("has_low_res = TRUE")

            cur.execute(
                f"""
                UPDATE systems
                SET {", ".join(set_parts)}
                WHERE name = %s
                  AND power_state = 'Expansion'
                  AND has_ring = TRUE
                RETURNING id64, is_candidate
                """,
                (name,),
            )
            row = cur.fetchone()
            if row:
                # Count as marked if it wasn't already a candidate
                marked += 1

    return marked


def update_candidacy() -> bool:
    """Run consolidated candidacy check.

    1. Reset is_candidate for non-Contest systems
    2. Query INARA and EDTools for all Expansion+ring systems
    3. Mark candidates and update RES info
    4. Query Siriuscorp for candidate RES data

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    console.print(f"[cyan]Running candidacy check for {power}...[/cyan]")
    console.print(f"[dim]INARA/EDTools radius: {CANDIDACY_QUERY_RADIUS_LY} ly[/dim]")
    console.print(f"[dim]Siriuscorp radius: {SIRIUSCORP_BOUNTY_QUERY_RADIUS_LY} ly[/dim]")
    console.print(f"[dim]Delay between queries: {QUERY_DELAY_SECONDS}s[/dim]")
    console.print()

    try:
        with psycopg.connect(DB_URL) as conn:
            # Step 1: Reset non-Contest candidates
            console.print("[cyan]Step 1:[/cyan] Resetting non-Contest candidates...")
            reset_count = _reset_non_contest_candidates(conn)
            conn.commit()
            console.print(f"  [dim]Reset {reset_count} systems[/dim]")
            console.print()

            # Step 2: Calculate reference systems
            console.print("[cyan]Step 2:[/cyan] Calculating reference systems...")
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

            console.print(f"  [dim]Found {total_targets} Expansion systems with rings[/dim]")

            reference_systems = find_reference_systems(conn, CANDIDACY_QUERY_RADIUS_LY)
            console.print(f"  [green]Need {len(reference_systems)} queries[/green]")
            console.print()

            # Step 3: Query INARA and EDTools for each reference system
            console.print("[cyan]Step 3:[/cyan] Querying INARA massacre and EDTools...")
            inara_pool: dict[str, dict] = {}
            edtools_pool: dict[str, dict] = {}

            for i, ref in enumerate(reference_systems):
                if i > 0:
                    console.print(f"[dim]Waiting {QUERY_DELAY_SECONDS}s...[/dim]")
                    time.sleep(QUERY_DELAY_SECONDS)

                console.print(
                    f"  [cyan]({i+1}/{len(reference_systems)})[/cyan] "
                    f"Querying {ref['name']}..."
                )

                # Query INARA
                inara_html = _fetch_inara_massacre(ref["name"])
                if inara_html:
                    inara_targets = _parse_inara_massacre_results(inara_html)
                    for name, res_info in inara_targets.items():
                        if name not in inara_pool:
                            inara_pool[name] = res_info
                    console.print(f"    [dim]INARA: {len(inara_targets)} targets[/dim]")
                else:
                    console.print(f"    [dim]INARA: failed[/dim]")

                # Query EDTools
                edtools_html = _fetch_edtools(ref["name"], CANDIDACY_QUERY_RADIUS_LY)
                if edtools_html:
                    edtools_targets = _parse_edtools_results(edtools_html)
                    for name, res_info in edtools_targets.items():
                        if name not in edtools_pool:
                            edtools_pool[name] = res_info
                    console.print(f"    [dim]EDTools: {len(edtools_targets)} targets[/dim]")
                else:
                    console.print(f"    [dim]EDTools: failed[/dim]")

            console.print()

            # Step 4: Compare pools
            console.print("[cyan]Step 4:[/cyan] Comparing INARA and EDTools pools...")
            edtools_only = set(edtools_pool.keys()) - set(inara_pool.keys())
            console.print(f"  Total INARA targets: {len(inara_pool)}")
            console.print(f"  Total EDTools targets: {len(edtools_pool)}")
            console.print(f"  [yellow]EDTools only (not in INARA): {len(edtools_only)}[/yellow]")
            if edtools_only and len(edtools_only) <= 20:
                for name in sorted(edtools_only):
                    console.print(f"    - {name}")
            console.print()

            # Step 5: Merge pools and mark candidates
            console.print("[cyan]Step 5:[/cyan] Marking candidates...")

            # Merge pools - EDTools now has RES info too
            combined_pool: dict[str, dict] = {}
            for name, res_info in inara_pool.items():
                combined_pool[name] = {
                    "has_high_res": res_info.get("has_high_res", False),
                    "has_med_res": False,  # INARA doesn't have med
                    "has_low_res": res_info.get("has_low_res", False),
                    "has_haz_res": res_info.get("has_haz_res", False),
                }
            for name, res_info in edtools_pool.items():
                if name in combined_pool:
                    combined_pool[name]["has_high_res"] |= res_info.get("has_high_res", False)
                    combined_pool[name]["has_med_res"] |= res_info.get("has_med_res", False)
                    combined_pool[name]["has_low_res"] |= res_info.get("has_low_res", False)
                    combined_pool[name]["has_haz_res"] |= res_info.get("has_haz_res", False)
                else:
                    combined_pool[name] = res_info.copy()

            marked = _mark_candidates_with_res(conn, combined_pool)
            conn.commit()
            console.print(f"  [green]Marked {marked} systems as candidates[/green]")
            console.print()

            # Step 6: Query Siriuscorp for RES data
            console.print("[cyan]Step 6:[/cyan] Querying Siriuscorp for RES data...")

            # Get all candidates
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, has_high_res, has_med_res, has_low_res
                    FROM systems
                    WHERE power_state = 'Expansion' AND is_candidate = TRUE
                """)
                candidates = cur.fetchall()

            if not candidates:
                console.print("  [dim]No candidates to query[/dim]")
            else:
                console.print(f"  [dim]Querying {len(candidates)} candidates[/dim]")

                siriuscorp_updates = 0
                for i, (cand_name, old_high, old_med, old_low) in enumerate(candidates):
                    if i > 0:
                        console.print(f"[dim]Waiting {QUERY_DELAY_SECONDS}s...[/dim]")
                        time.sleep(QUERY_DELAY_SECONDS)

                    console.print(
                        f"  [cyan]({i+1}/{len(candidates)})[/cyan] "
                        f"Siriuscorp: {cand_name}..."
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
                        console.print(f"    [dim]Not found in response[/dim]")
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

                        # Debug output for Siriuscorp-only RES info
                        res_parts = []
                        if new_high:
                            res_parts.append("H")
                        if new_med:
                            res_parts.append("M")
                        if new_low:
                            res_parts.append("L")
                        console.print(
                            f"    [yellow]Siriuscorp-only RES:[/yellow] +{''.join(res_parts)}"
                        )
                        siriuscorp_updates += 1

                console.print()
                console.print(f"  [dim]Siriuscorp-only RES updates: {siriuscorp_updates}[/dim]")

            console.print()
            console.print("[green]Candidacy check complete![/green]")

            # Final summary
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM systems
                    WHERE power_state = 'Expansion' AND is_candidate = TRUE
                """)
                final_count = cur.fetchone()[0]

            console.print(f"  Total candidates: {final_count}")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
