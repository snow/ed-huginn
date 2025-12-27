"""INARA power history scraper - detects systems that changed state."""

import re
from datetime import datetime, timezone

import psycopg
import requests
from bs4 import BeautifulSoup
from rich.console import Console

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


def _parse_state_transition(state_text: str) -> tuple[str, str] | None:
    """Parse state transition text like 'Expansion > Exploited'.

    Returns tuple of (before_state, after_state) or None if parsing fails.
    """
    # The separator is a special arrow character (U+E833 or similar)
    # Try multiple patterns
    patterns = [
        r"(.+?)\s*[︎>→]\s*(.+)",  # Arrow characters
        r"(.+?)\s+>\s+(.+)",  # Simple >
    ]

    for pattern in patterns:
        match = re.match(pattern, state_text.strip())
        if match:
            before = match.group(1).strip()
            after = match.group(2).strip()
            return before, after

    return None


def _parse_history_page(html: str) -> list[dict]:
    """Parse power history table from INARA HTML.

    Returns list of dicts with: name, before_state, after_state, updated_at
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the table with class tablesorter
    table = soup.find("table", class_="tablesorter")
    if not table:
        return []

    tbody = table.find("tbody")
    if not tbody:
        return []

    transitions = []
    rows = tbody.find_all("tr")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        # Cell 0: System name (link)
        name_link = cells[0].find("a")
        if not name_link:
            continue
        name = clean_system_name(name_link.get_text(strip=True))

        # Cell 2: State transition (e.g., "Expansion > Exploited")
        state_text = cells[2].get_text(strip=True)
        transition = _parse_state_transition(state_text)
        if not transition:
            continue
        before_state, after_state = transition

        # Cell 3: Updated timestamp (data-order attribute)
        updated_ts = cells[3].get("data-order")
        if not updated_ts:
            continue

        try:
            updated_at = datetime.fromtimestamp(int(updated_ts), tz=timezone.utc)
        except (ValueError, TypeError):
            continue

        transitions.append({
            "name": name,
            "before_state": before_state,
            "after_state": after_state,
            "updated_at": updated_at,
        })

    return transitions


def update_from_history() -> bool:
    """Fetch power history from INARA and update systems that changed state.

    Updates power_state for all transitions. Does NOT modify is_candidate
    (that's managed by the candidacy service).

    Returns True if successful.
    """
    power = get_pledged_power()
    if not power:
        console.print("[red]No pledged power set.[/red] Use 'Set pledged power' first.")
        return False

    url = get_power_url(power, "history")
    console.print(f"[cyan]Fetching power history for {power}...[/cyan]")
    console.print(f"[dim]{url}[/dim]")
    console.print()

    html = _fetch_page(url)
    if not html:
        return False

    transitions = _parse_history_page(html)
    console.print(f"[dim]Found {len(transitions)} state transitions[/dim]")

    if not transitions:
        console.print("[green]No transitions to process.[/green]")
        return True

    try:
        with psycopg.connect(DB_URL) as conn:
            updated = 0
            skipped = 0
            not_found_names = []

            for t in transitions:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id64, inara_updated_at FROM systems WHERE name = %s",
                        (t["name"],),
                    )
                    row = cur.fetchone()

                if not row:
                    not_found_names.append(t["name"])
                    continue

                db_id64, db_updated = row

                # Check if INARA data is newer
                if db_updated is not None:
                    if db_updated.tzinfo is None:
                        db_updated = db_updated.replace(tzinfo=timezone.utc)
                    if t["updated_at"] <= db_updated:
                        skipped += 1
                        continue

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE systems
                        SET power = %s,
                            power_state = %s,
                            inara_updated_at = %s,
                            updated_at = NOW()
                        WHERE id64 = %s
                        """,
                        (power, t["after_state"], t["updated_at"], db_id64),
                    )

                console.print(
                    f"  {t['name']}: {t['before_state']} → {t['after_state']}"
                )
                updated += 1

            conn.commit()

            console.print()
            console.print(f"Updated: {updated}, Skipped: {skipped}, Not in DB: {len(not_found_names)}")
            if not_found_names:
                for name in not_found_names:
                    console.print(f"  [dim]{name}[/dim]")
            console.print("[green]Done![/green]")
            return True

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return False
