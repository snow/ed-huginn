"""Scheduler for periodic incremental updates.

Runs as a long-lived process, spawning subprocesses for each update cycle.
Subprocess isolation ensures memory is fully released between runs.
"""

import subprocess
import sys
import time
from datetime import datetime

import schedule
from rich.console import Console

console = Console()


def run_update_subprocess() -> bool:
    """Spawn subprocess to run incremental-update command.

    Returns True if subprocess succeeded.
    Skips if enable_periodical_update is not explicitly true in config.
    """
    from huginn.config import load_config

    config = load_config()
    if not config.get("enable_periodical_update", False):
        return True  # Skip silently, not an error

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"[cyan][{timestamp}][/cyan] Starting incremental update...")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "huginn", "incremental-update"],
            capture_output=False,  # Let output flow to console
            timeout=1800,  # 30 minute timeout
        )

        if result.returncode == 0:
            console.print(f"[green][{timestamp}][/green] Update completed successfully")
            return True
        else:
            console.print(f"[red][{timestamp}][/red] Update failed with code {result.returncode}")
            return False

    except subprocess.TimeoutExpired:
        console.print(f"[red][{timestamp}][/red] Update timed out after 30 minutes")
        return False
    except Exception as e:
        console.print(f"[red][{timestamp}][/red] Update error: {e}")
        return False


def start_scheduler(interval_hours: float = 3.0, run_immediately: bool = True):
    """Start the scheduler loop.

    Args:
        interval_hours: Hours between update runs
        run_immediately: If True, run an update immediately before starting schedule
    """
    console.print()
    console.print("[bold cyan]Huginn Scheduler[/bold cyan]")
    console.print(f"[dim]Running incremental-update every {interval_hours} hour(s)[/dim]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")
    console.print()

    if run_immediately:
        console.print("[dim]Running initial update...[/dim]")
        run_update_subprocess()

    # Schedule periodic runs
    if interval_hours >= 1:
        schedule.every(int(interval_hours)).hours.do(run_update_subprocess)
    else:
        # For testing: allow minute-level intervals
        minutes = int(interval_hours * 60)
        schedule.every(minutes).minutes.do(run_update_subprocess)

    console.print()
    console.print("[dim]Scheduler running. Waiting for next cycle...[/dim]")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    except KeyboardInterrupt:
        console.print()
        console.print("[dim]Scheduler stopped.[/dim]")
