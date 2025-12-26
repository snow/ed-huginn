"""Huginn CLI - Elite Dangerous intelligence gatherer."""

import sys

from rich.console import Console
from rich.panel import Panel
from simple_term_menu import TerminalMenu

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
    console.print()
    console.print("[bold cyan]Huginn[/bold cyan] - Elite Dangerous Intelligence Gatherer")
    console.print()

    # Filter to visible items
    visible_items = []
    for item in MENU_ITEMS:
        label, command, func, enabled, visible_check = item
        if visible_check is None or visible_check():
            visible_items.append(item)

    menu_entries = []
    for label, _, _, enabled, _ in visible_items:
        if enabled:
            menu_entries.append(label)
        else:
            menu_entries.append(f"[dim] {label} (WIP)")

    menu_entries.append("Quit")

    menu = TerminalMenu(
        menu_entries,
        title="Select an option:\n",
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("bg_cyan", "fg_black"),
        cycle_cursor=True,
        clear_screen=False,
    )

    choice = menu.show()

    if choice is None or choice == len(visible_items):
        console.print("[dim]Goodbye![/dim]")
        return 0

    label, _, func, enabled, _ = visible_items[choice]

    if not enabled:
        console.print(f"[yellow]{label} is not yet implemented.[/yellow]")
        return 1

    console.print()
    return func()


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
    menu_entries = power_names + ["Cancel"]

    menu = TerminalMenu(
        menu_entries,
        title="Select your pledged power:\n",
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("bg_cyan", "fg_black"),
        cycle_cursor=True,
        clear_screen=False,
    )

    choice = menu.show()

    if choice is None or choice == len(power_names):
        console.print("[dim]Cancelled.[/dim]")
        return 0

    selected = power_names[choice]
    set_pledged_power(selected)
    console.print(f"[green]Pledged to {selected}![/green]")
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


@register_menu("Update Siriuscorp data", "siriuscorp", visible=_has_pledged_power)
def update_siriuscorp():
    """Query Siriuscorp for RES site data and update database."""
    from huginn.services.siriuscorp import update_from_siriuscorp

    success = update_from_siriuscorp()
    return 0 if success else 1


@register_menu("List candidates", "candidates", enabled=False)
def candidates():
    """List candidate systems for AFK bounty hunting."""
    console.print(
        Panel(
            "[dim]This feature is under development.[/dim]\n\n"
            "Will list systems matching:\n"
            "• Unoccupied powerplay state\n"
            "• Within range of controlled space\n"
            "• Has RES sites (Low/Normal/High)\n"
            "• Inhabited (has stations)",
            title="[bold yellow]WIP[/bold yellow] List Candidates",
        )
    )
    return 1


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
