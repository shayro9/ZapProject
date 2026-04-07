"""Zap client onboarding automation pipeline — CLI entrypoint."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.json import JSON
from rich.table import Table
from rich.text import Text

from src.url_discovery import discover_urls, DiscoveryError
from src.scraper import scrape_urls
from src.extractor import extract_client_card, ExtractionError
from src.call_script import generate_call_script, CallScriptError
from src import crm as crm_module
from src.notifier import send_call_script

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = typer.Typer(help="Zap client onboarding automation pipeline")
console = Console()


@app.command()
def run(
    url: list[str] = typer.Option(
        [],
        "--url",
        help="Client URL(s). Can be repeated (e.g. --url https://... --url https://...).",
    ),
    business: Optional[str] = typer.Option(
        None,
        "--business",
        help="Business name (used for URL discovery when no --url given).",
    ),
    phone: Optional[str] = typer.Option(
        None,
        "--phone",
        help="Business phone number (narrows URL discovery).",
    ),
    email: str = typer.Option(
        ...,
        "--email",
        help="Zap producer email address — the call script is sent here, not to the client.",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="CRM SQLite DB path (default: CRM_DB_PATH env var, or 'crm.db').",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip email sending and CRM insert (useful for testing).",
    ),
) -> None:
    """Run the full onboarding pipeline for a single business client."""
    asyncio.run(_run_pipeline(list(url), business, phone, email, db_path, dry_run))


# ---------------------------------------------------------------------------
# Core async pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline(
    urls: list[str],
    business: Optional[str],
    phone: Optional[str],
    email: str,
    db_path: Optional[str],
    dry_run: bool,
) -> None:
    """Execute the end-to-end onboarding pipeline."""

    # --- 0. Resolve DB path ---------------------------------------------------
    resolved_db = db_path or os.environ.get("CRM_DB_PATH", "crm.db")

    # --- 1. Validate inputs ---------------------------------------------------
    if not urls and not business:
        console.print(
            "[bold red]Error:[/bold red] Provide at least one --url OR --business name."
        )
        raise typer.Exit(code=1)

    # --- 2. URL discovery (if no URLs provided) --------------------------------
    if not urls:
        console.print(
            f"[cyan]🔍 Discovering URLs for:[/cyan] [bold]{business}[/bold]"
        )
        try:
            urls = await discover_urls(business, phone)  # type: ignore[arg-type]
        except DiscoveryError as exc:
            console.print(f"[bold red]Discovery failed:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc

        if not urls:
            console.print("[yellow]⚠ No URLs discovered. Cannot continue.[/yellow]")
            raise typer.Exit(code=3)

        console.print(
            Panel(
                "\n".join(f"  • {u}" for u in urls),
                title="[green]Discovered URLs[/green]",
                expand=False,
            )
        )

    # --- 3. Scrape URLs -------------------------------------------------------
    console.print(f"[cyan]🌐 Scraping {len(urls)} URL(s)…[/cyan]")
    texts = await scrape_urls(urls)
    scraped_count = sum(1 for t in texts.values() if t.strip())
    console.print(
        f"[green]✓[/green] Scraped {scraped_count}/{len(urls)} pages successfully."
    )

    # --- 4. Extract client card -----------------------------------------------
    console.print("[cyan]🤖 Extracting client data with GPT-4o…[/cyan]")
    try:
        client_card = await extract_client_card(texts, urls)
    except ExtractionError as exc:
        console.print(f"[bold red]Extraction failed:[/bold red] {exc}")
        raise typer.Exit(code=4) from exc

    console.print(
        Panel(
            JSON(client_card.model_dump_json(indent=2)),
            title=f"[green]Client Card — {client_card.business_name}[/green]",
            expand=False,
        )
    )

    # --- 5. Generate call script ----------------------------------------------
    console.print("[cyan]✍️  Generating Hebrew call script with GPT-4o…[/cyan]")
    try:
        call_script = await generate_call_script(client_card)
    except CallScriptError as exc:
        console.print(f"[bold red]Script generation failed:[/bold red] {exc}")
        raise typer.Exit(code=5) from exc

    console.print(
        Panel(
            Text(call_script),
            title="[green]Call Script (Hebrew)[/green]",
            expand=False,
        )
    )

    # --- 6. CRM insert + email (unless dry-run) --------------------------------
    if dry_run:
        console.print(
            "[yellow]⚡ Dry-run mode:[/yellow] Skipping CRM insert and email notification."
        )
    else:
        # Init DB (idempotent)
        crm_module.init_db(resolved_db)

        from models.schemas import CRMRecord
        record = CRMRecord(
            client_card=client_card,
            call_script=call_script,
            notified=False,
        )

        record_id = crm_module.insert_record(resolved_db, record)
        console.print(
            f"[green]✓[/green] CRM record inserted (id={record_id}) in '{resolved_db}'."
        )

        # Send the call script to the Zap producer
        notify_to = email
        sent = await send_call_script(
            to_email=notify_to,
            client_name=client_card.business_name,
            call_script=call_script,
        )

        if sent:
            console.print(
                f"[green]✓[/green] Call script emailed to [bold]{notify_to}[/bold]."
            )
            record.notified = True
            crm_module.update_notified(resolved_db, record_id, notified=True)
        else:
            console.print(
                f"[yellow]⚠[/yellow] Email notification failed for {notify_to}."
            )

    # --- 7. Final summary ------------------------------------------------------
    console.print(
        Panel(
            f"[bold green]✅ Onboarding pipeline complete[/bold green]\n"
            f"Business : {client_card.business_name}\n"
            f"Area     : {client_card.area or 'N/A'}\n"
            f"Services : {len(client_card.services)}\n"
            f"Email    : {email}\n"
            f"Dry-run  : {dry_run}",
            title="Summary",
            expand=False,
        )
    )


@app.command(name="list")
def list_records_cmd(
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="CRM SQLite DB path (default: CRM_DB_PATH env var, or 'crm.db').",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Maximum number of records to display (newest first).",
    ),
) -> None:
    """List onboarding records stored in the CRM database."""
    resolved_db = db_path or os.environ.get("CRM_DB_PATH", "crm.db")

    if not Path(resolved_db).exists():
        console.print(
            f"[yellow]⚠ Database not found:[/yellow] [bold]{resolved_db}[/bold]\n"
            "Run the pipeline first to create it."
        )
        raise typer.Exit(code=1)

    records = crm_module.list_records(resolved_db, limit=limit)

    if not records:
        console.print("[yellow]No records found in the CRM.[/yellow]")
        return

    table = Table(
        title=f"CRM Onboarding Records — {resolved_db} ({len(records)} total)",
        show_lines=True,
    )
    table.add_column("ID", style="cyan", no_wrap=True, justify="right")
    table.add_column("Business", style="bold")
    table.add_column("Area")
    table.add_column("Services", justify="center")
    table.add_column("Notified", justify="center")
    table.add_column("Created At", style="dim")

    for record in records:
        table.add_row(
            str(record.id),
            record.client_card.business_name,
            record.client_card.area or "—",
            str(len(record.client_card.services)),
            "✅" if record.notified else "❌",
            record.created_at.strftime("%Y-%m-%d  %H:%M"),
        )

    console.print(table)


if __name__ == "__main__":
    app()