"""Huginn CLI - Elite Dangerous intelligence gatherer."""

import sys

import questionary
from rich.console import Console

console = Console()

# Menu items: (label, command_name, command_func, enabled, visible_check)
# visible_check is optional callable that returns True if item should be shown
MENU_ITEMS: list[tuple[str, str, callable, bool, callable | None]] = []


def register_menu(label: str, command: str, enabled: bool = True, visible: callable = None):
    """Decorator to register a command in the interactive menu.

    Args:
        label: Display label in menu
        command: CLI command name
        enabled: Whether the command is implemented
        visible: Optional callable that returns True if item should be shown
    """
    def decorator(func):
        MENU_ITEMS.append((label, command, func, enabled, visible))
        return func
    return decorator


def show_menu():
    """Display interactive menu and handle selection."""
    while True:
        console.print()
        console.print("[bold cyan]Huginn[/bold cyan] - Elite Dangerous Intelligence Gatherer")
        console.print()

        # Filter to visible items
        visible_items = []
        for item in MENU_ITEMS:
            label, command, func, enabled, visible_check = item
            if visible_check is None or visible_check():
                visible_items.append(item)

        choices = []
        for label, _, _, enabled, _ in visible_items:
            if enabled:
                choices.append(label)
            else:
                choices.append(f"{label} (WIP)")

        choices.append("Quit")

        choice = questionary.select(
            "Select an option:",
            choices=choices,
        ).ask()

        if choice is None or choice == "Quit":
            console.print("[dim]Goodbye![/dim]")
            return 0

        # Find selected item
        for label, _, func, enabled, _ in visible_items:
            display_label = label if enabled else f"{label} (WIP)"
            if choice == display_label:
                if not enabled:
                    console.print(f"[yellow]{label} is not yet implemented.[/yellow]")
                    break
                console.print()
                func()
                break


def show_help():
    """Display help message."""
    console.print()
    console.print("[bold cyan]Huginn[/bold cyan] - Elite Dangerous Intelligence Gatherer")
    console.print()
    console.print("Usage: python -m huginn [command]")
    console.print()
    console.print("Commands:")
    for label, command, _, enabled, _ in MENU_ITEMS:
        status = "" if enabled else " [dim](WIP)[/dim]"
        console.print(f"  {command:12} {label}{status}")
    console.print()
    console.print("Run without arguments for interactive menu.")
    return 0


@register_menu("Seed database", "seed")
def seed():
    """Seed the database with Spansh galaxy data."""
    from huginn.services.seeder import seed_database

    success = seed_database()
    return 0 if success else 1


@register_menu("Set pledged power", "power")
def set_power():
    """Set your pledged power for filtering systems."""
    from huginn.config import POWERS, get_pledged_power, set_pledged_power

    current = get_pledged_power()
    if current:
        console.print(f"[dim]Currently pledged to:[/dim] [cyan]{current}[/cyan]")
        console.print()

    power_names = sorted(POWERS.keys())
    choices = power_names + ["Cancel"]

    choice = questionary.select(
        "Select your pledged power:",
        choices=choices,
    ).ask()

    if choice is None or choice == "Cancel":
        console.print("[dim]Cancelled.[/dim]")
        return 0

    set_pledged_power(choice)
    console.print(f"[green]Pledged to {choice}![/green]")
    return 0


def _has_pledged_power() -> bool:
    """Check if user has set a pledged power."""
    from huginn.config import get_pledged_power
    return get_pledged_power() is not None


@register_menu("Update INARA data", "inara", visible=_has_pledged_power)
def update_inara():
    """Fetch contested systems from INARA and update database."""
    from huginn.services.inara import update_from_inara

    success = update_from_inara()
    return 0 if success else 1


@register_menu("Update candidates", "check", visible=_has_pledged_power)
def update_candidates():
    """Run consolidated candidacy check (INARA massacre + EDTools + Siriuscorp)."""
    from huginn.services.candidacy import update_candidacy

    success = update_candidacy()
    return 0 if success else 1


def _get_last_thursday_tick():
    """Get the datetime of the last Thursday tick (07:00 UTC)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    # Thursday is weekday 3
    days_since_thursday = (now.weekday() - 3) % 7
    last_thursday = now - timedelta(days=days_since_thursday)
    # Set to 07:00 UTC (tick time)
    tick_time = last_thursday.replace(hour=7, minute=0, second=0, microsecond=0)
    # If we're before Thursday 07:00 this week, go back another week
    if tick_time > now:
        tick_time -= timedelta(days=7)
    return tick_time


@register_menu("List candidates", "candidates", visible=_has_pledged_power)
def candidates():
    """List candidate systems for AFK bounty hunting."""
    import subprocess
    import urllib.parse
    from datetime import timezone

    import psycopg

    from huginn.services.utils import DB_URL

    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, power_state, has_high_res, has_med_res, has_low_res,
                           inara_updated_at, metadata
                    FROM systems
                    WHERE is_candidate = TRUE
                    ORDER BY inara_updated_at DESC NULLS LAST
                """)
                rows = cur.fetchall()

        if not rows:
            console.print("[yellow]No candidate systems found.[/yellow]")
            console.print("[dim]Run the scrapers first to find candidates.[/dim]")
            return 0

        last_tick = _get_last_thursday_tick()

        # Build menu entries and URL mapping
        choices = []
        url_map = {}
        for name, power_state, has_high, has_med, has_low, inara_updated_at, metadata in rows:
            encoded_name = urllib.parse.quote(name)
            inara_url = f"https://inara.cz/elite/nearest-misc/?ps1={encoded_name}&pi20=9"

            # RES info: HML, -M-, --L, ---
            res_str = f"{'H' if has_high else '-'}{'M' if has_med else '-'}{'L' if has_low else '-'}"

            # Source factions string (e.g., "5+4+3=12")
            factions_str = ""
            if metadata and isinstance(metadata, dict):
                factions_str = metadata.get("source_factions", "")
            factions_display = f" [{factions_str}]" if factions_str else ""

            # Format timestamp as "Dec 27 01:28"
            if inara_updated_at:
                ts_str = inara_updated_at.strftime("%b %d %H:%M")
                ts_aware = inara_updated_at.replace(tzinfo=timezone.utc) if inara_updated_at.tzinfo is None else inara_updated_at
                is_stale = ts_aware < last_tick
            else:
                ts_str = "N/A"
                is_stale = True

            # Mark stale entries with * prefix
            stale_marker = "  " if not is_stale else "* "
            entry = f"{stale_marker}{name:<30} {power_state:<12} {res_str} {factions_display:<16} {ts_str}"
            choices.append(entry)
            url_map[entry] = inara_url

        choices.append("Back")

        console.print(f"[cyan]Found {len(rows)} candidate systems[/cyan]")
        console.print("[dim]Select to copy INARA massacre link, Ctrl+C to go back[/dim]")
        console.print()

        while True:
            choice = questionary.select(
                "Candidate Systems:",
                choices=choices,
            ).ask()

            if choice is None or choice == "Back":
                return 0

            selected_url = url_map[choice]

            # Copy INARA URL to clipboard
            try:
                subprocess.run(["pbcopy"], input=selected_url.encode(), check=True)
                console.print(f"[green]Copied:[/green] {selected_url}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=selected_url.encode(), check=True)
                    console.print(f"[green]Copied:[/green] {selected_url}")
                except (subprocess.CalledProcessError, FileNotFoundError):
                    console.print(f"[yellow]Could not copy to clipboard.[/yellow]")
                    console.print(f"URL: {selected_url}")

    except psycopg.Error as e:
        console.print(f"[red]Database error:[/red] {e}")
        return 1

    return 0


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        sys.exit(show_menu())

    cmd = sys.argv[1]

    if cmd in ("--help", "-h", "help"):
        sys.exit(show_help())

    # Build command lookup
    commands = {command: (func, enabled) for _, command, func, enabled, _ in MENU_ITEMS}

    if cmd not in commands:
        console.print(f"[red]Unknown command:[/red] {cmd}")
        console.print("Run with --help for usage.")
        sys.exit(1)

    func, enabled = commands[cmd]
    if not enabled:
        console.print(f"[yellow]{cmd} is not yet implemented.[/yellow]")
        sys.exit(1)

    sys.exit(func())


if __name__ == "__main__":
    main()
