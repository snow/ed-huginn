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
)
from huginn.services.utils import (
    DB_URL,
    USER_AGENT,
    QUERY_DELAY_SECONDS,
    clean_system_name,
    find_reference_systems,
)

INARA_MASSACRE_URL = "https://inara.cz/elite/nearest-misc/"
EDTOOLS_URL = "https://edtools.cc/pve"

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


def _fetch_inara_system(url: str) -> str | None:
    """Fetch INARA system detail page."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        console.print(f"[red]INARA system fetch failed:[/red] {e}")
        return None


def _parse_inara_system_factions(html: str) -> int:
    """Parse INARA system page and count factions NOT in War/Civil war/Elections.

    These states prevent factions from giving pirate massacre missions.
    Returns count of "peaceful" factions (those that can give massacre missions).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the faction table (has headers: Faction, Government, Allegiance, Pending, Active, Inf)
    tables = soup.find_all("table", class_="tablesorter")
    for table in tables:
        thead = table.find("thead")
        if not thead:
            continue
        headers = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
        if "faction" not in headers or "active" not in headers:
            continue

        # Found the faction table
        tbody = table.find("tbody")
        if not tbody:
            return 0

        peaceful_count = 0
        rows = tbody.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            # Active states column (index 4)
            active_cell = cells[4]
            state_tags = active_cell.find_all("span", class_=lambda c: c and "statetag" in c)
            states = [tag.get_text(strip=True).lower() for tag in state_tags]

            # Check if faction is in war/civil war/elections
            is_blocked = any(
                s in ("war", "civil war", "elections")
                for s in states
            )

            if not is_blocked:
                peaceful_count += 1

        return peaceful_count

    return 0


def _parse_inara_massacre_results(html: str) -> dict[str, dict]:
    """Parse INARA massacre results.

    Returns dict of {system_name: {
        "has_high_res": bool,
        "has_low_res": bool,
        "has_haz_res": bool,
        "sources": {source_name: source_url, ...}
    }}.
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

        # Cell 0: SOURCE STAR SYSTEM - contains links to source systems
        source_cell = cells[0]
        source_links = source_cell.find_all("a", href=lambda h: h and "/starsystem/" in h)
        sources = {}
        for link in source_links:
            src_name = clean_system_name(link.get_text(strip=True))
            src_href = link.get("href", "")
            if src_name and src_href:
                # Build full URL from relative href like /elite/starsystem/1728/
                sources[src_name] = f"https://inara.cz{src_href}"

        # Cell 4: TARGET system
        system_cell = cells[4]
        system_link = system_cell.find("a", href=lambda h: h and "/starsystem/" in h)
        if system_link:
            system_name = clean_system_name(system_link.get_text(strip=True))
            if not system_name or system_name in target_systems:
                continue

            tags = system_cell.find_all("span", class_="tag")
            tag_texts = [tag.get_text(strip=True).lower() for tag in tags]

            has_high_res = any("high res" in t for t in tag_texts)
            has_low_res = any("low res" in t for t in tag_texts)
            has_haz_res = any("haz res" in t for t in tag_texts)

            target_systems[system_name] = {
                "has_high_res": has_high_res,
                "has_low_res": has_low_res,
                "has_haz_res": has_haz_res,
                "sources": sources,
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
        if not system_name or system_name in target_systems:
            continue

        # RES info is in column 11 (index 10)
        # Format: <a href="res?s=...">haz,high,reg,low</a> or just "2 rings" / "no rings"
        res_cell = cells[10]
        res_text = res_cell.get_text(strip=True).lower()

        has_high_res = "high" in res_text
        has_med_res = "reg" in res_text  # "reg" = regular = medium
        has_low_res = "low" in res_text
        has_haz_res = "haz" in res_text

        target_systems[system_name] = {
            "has_high_res": has_high_res,
            "has_med_res": has_med_res,
            "has_low_res": has_low_res,
            "has_haz_res": has_haz_res,
        }

    return target_systems


def _reset_non_contest_candidates(conn) -> int:
    """Reset is_candidate = FALSE for all non-Contest systems.

    Returns count of systems reset.
    """
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE systems
            SET is_candidate = FALSE, updated_at = NOW()
            WHERE power_state != 'Contested'
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
    4. Count peaceful factions in source systems

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    console.print(f"[cyan]Running candidacy check for {power}...[/cyan]")
    console.print(f"[dim]INARA/EDTools radius: {CANDIDACY_QUERY_RADIUS_LY} ly[/dim]")
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
            # Keep sources from INARA pool for faction counting
            combined_pool: dict[str, dict] = {}
            for name, res_info in inara_pool.items():
                combined_pool[name] = {
                    "has_high_res": res_info.get("has_high_res", False),
                    "has_med_res": False,  # INARA doesn't have med
                    "has_low_res": res_info.get("has_low_res", False),
                    "has_haz_res": res_info.get("has_haz_res", False),
                    "sources": res_info.get("sources", {}),
                }
            for name, res_info in edtools_pool.items():
                if name in combined_pool:
                    combined_pool[name]["has_high_res"] |= res_info.get("has_high_res", False)
                    combined_pool[name]["has_med_res"] |= res_info.get("has_med_res", False)
                    combined_pool[name]["has_low_res"] |= res_info.get("has_low_res", False)
                    combined_pool[name]["has_haz_res"] |= res_info.get("has_haz_res", False)
                else:
                    # EDTools doesn't have source info
                    combined_pool[name] = {
                        "has_high_res": res_info.get("has_high_res", False),
                        "has_med_res": res_info.get("has_med_res", False),
                        "has_low_res": res_info.get("has_low_res", False),
                        "has_haz_res": res_info.get("has_haz_res", False),
                        "sources": {},
                    }

            marked = _mark_candidates_with_res(conn, combined_pool)
            conn.commit()
            console.print(f"  [green]Marked {marked} systems as candidates[/green]")
            console.print()

            # Step 6: Fetch source system faction counts
            console.print("[cyan]Step 6:[/cyan] Counting peaceful factions in source systems...")

            # Get candidates that have sources
            candidates_with_sources = []
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name FROM systems
                    WHERE power_state = 'Expansion' AND is_candidate = TRUE
                """)
                for (cand_name,) in cur.fetchall():
                    if cand_name in combined_pool and combined_pool[cand_name].get("sources"):
                        candidates_with_sources.append(
                            (cand_name, combined_pool[cand_name]["sources"])
                        )

            if not candidates_with_sources:
                console.print("  [dim]No candidates with source systems[/dim]")
            else:
                console.print(f"  [dim]Processing {len(candidates_with_sources)} candidates[/dim]")

                # Cache: URL -> peaceful faction count (avoid refetching same source)
                source_cache: dict[str, int] = {}

                for cand_name, sources in candidates_with_sources:
                    faction_counts = []

                    for src_name, src_url in sources.items():
                        if src_url in source_cache:
                            count = source_cache[src_url]
                        else:
                            console.print(f"    [dim]Fetching {src_name}...[/dim]")
                            time.sleep(QUERY_DELAY_SECONDS)
                            html = _fetch_inara_system(src_url)
                            if html:
                                count = _parse_inara_system_factions(html)
                            else:
                                count = 0
                            source_cache[src_url] = count

                        faction_counts.append(count)

                    # Build faction string: "5+4+3+2=14" (sorted descending)
                    if faction_counts:
                        faction_counts.sort(reverse=True)
                        total = sum(faction_counts)
                        faction_str = "+".join(str(c) for c in faction_counts) + f"={total}"

                        # Update metadata
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE systems
                                SET metadata = jsonb_set(
                                    COALESCE(metadata, '{}'::jsonb),
                                    '{source_factions}',
                                    %s::jsonb
                                ),
                                updated_at = NOW()
                                WHERE name = %s
                                """,
                                (f'"{faction_str}"', cand_name),
                            )
                        conn.commit()
                        console.print(f"  {cand_name}: {faction_str}")

                console.print(f"  [dim]Cached {len(source_cache)} source systems[/dim]")

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
