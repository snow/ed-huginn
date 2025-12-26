"""Huginn CLI - Elite Dangerous intelligence gatherer."""

import sys

from rich.console import Console
from rich.panel import Panel
from simple_term_menu import TerminalMenu

console = Console()

# Menu items: (label, command_name, command_func, enabled)
MENU_ITEMS: list[tuple[str, str, callable, bool]] = []


def register_menu(label: str, command: str, enabled: bool = True):
    """Decorator to register a command in the interactive menu."""
    def decorator(func):
        MENU_ITEMS.append((label, command, func, enabled))
        return func
    return decorator


def show_menu():
    """Display interactive menu and handle selection."""
    console.print()
    console.print("[bold cyan]Huginn[/bold cyan] - Elite Dangerous Intelligence Gatherer")
    console.print()

    menu_entries = []
    for label, _, _, enabled in MENU_ITEMS:
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

    if choice is None or choice == len(MENU_ITEMS):
        console.print("[dim]Goodbye![/dim]")
        return 0

    label, _, func, enabled = MENU_ITEMS[choice]

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
    for label, command, _, enabled in MENU_ITEMS:
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
    commands = {command: (func, enabled) for _, command, func, enabled in MENU_ITEMS}

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
