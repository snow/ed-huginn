"""General incremental update - combines power history, candidacy, and Siriuscorp updates."""

from rich.console import Console

console = Console()


def run_incremental_update() -> bool:
    """Run all incremental update steps in sequence.

    Steps:
    1. Update power history from INARA
    2. Recalculate candidates
    3. Update RES from Siriuscorp

    Returns:
        True if all steps succeeded, False if any step failed.
    """
    from huginn.config import get_pledged_power
    from huginn.services.candidacy import update_candidacy
    from huginn.services.inara_power_history import update_from_history
    from huginn.services.siriuscorp import update_res_from_siriuscorp
    from huginn.services.utils import is_db_seeded

    if not is_db_seeded():
        console.print("[red]Database not seeded. Run 'seed' first.[/red]")
        return False

    if not get_pledged_power():
        console.print("[red]No pledged power set. Run 'power' first.[/red]")
        return False

    console.print("[bold cyan]Starting general incremental update...[/bold cyan]")
    console.print()

    # Step 1: Update power history from INARA
    console.print("[cyan]Step 1/3:[/cyan] Updating power history from INARA...")
    if not update_from_history():
        console.print("[red]Failed to update power history.[/red]")
        return False
    console.print()

    # Step 2: Recalculate candidates
    console.print("[cyan]Step 2/3:[/cyan] Recalculating candidates...")
    if not update_candidacy():
        console.print("[red]Failed to recalculate candidates.[/red]")
        return False
    console.print()

    # Step 3: Update RES from Siriuscorp
    console.print("[cyan]Step 3/3:[/cyan] Updating RES from Siriuscorp...")
    if not update_res_from_siriuscorp():
        console.print("[red]Failed to update RES from Siriuscorp.[/red]")
        return False
    console.print()

    console.print("[bold green]General incremental update complete![/bold green]")
    return True
