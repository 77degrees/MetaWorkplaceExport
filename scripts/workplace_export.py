"""Utility CLI and interactive wizard for the Workplace DIY Export API."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

DEFAULT_API_VERSION = "v17.0"
GRAPH_BASE = "https://graph.facebook.com/{version}/{path}"

console = Console()


class ExportClientError(RuntimeError):
    """Raised when the Workplace Export client receives an API error."""


def build_session(access_token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {access_token}"})
    return session


def graph_url(path: str, *, api_version: str = DEFAULT_API_VERSION) -> str:
    return GRAPH_BASE.format(version=api_version, path=path.lstrip("/"))


def paged_get(
    session: requests.Session, url: str, *, params: Optional[Dict[str, str]] = None
) -> Iterable[Dict]:
    while url:
        response = session.get(url, params=params)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ExportClientError(str(exc)) from exc
        payload = response.json()
        yield from payload.get("data", [])
        paging = payload.get("paging", {})
        url = paging.get("next")
        params = None  # subsequent pages already encoded in "next"


def fetch_app_token(app_id: str, app_secret: str, api_version: str) -> str:
    """Exchange an App ID/Secret for an application access token."""
    url = graph_url("oauth/access_token", api_version=api_version)
    response = requests.get(
        url,
        params={
            "grant_type": "client_credentials",
            "client_id": app_id,
            "client_secret": app_secret,
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ExportClientError(
            "Failed to fetch access token. Please verify the App ID/Secret and permissions."
        ) from exc
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise ExportClientError("The token response did not include an access token.")
    return token


def fetch_tenant_id(session: requests.Session, api_version: str) -> str:
    """Return the Workplace tenant/community ID."""
    url = graph_url("community", api_version=api_version)
    response = session.get(url)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ExportClientError("Unable to fetch tenant/community ID.") from exc
    data = response.json()
    tenant_id = data.get("id")
    if not tenant_id:
        raise ExportClientError("Response did not include a tenant/community ID.")
    return str(tenant_id)


def fetch_exports(
    session: requests.Session,
    tenant_id: str,
    api_version: str,
    status: Optional[str] = None,
) -> List[Dict]:
    url = graph_url(f"{tenant_id}/diy_exports", api_version=api_version)
    params: Dict[str, str] = {}
    if status:
        params["status"] = status
    return list(paged_get(session, url, params=params))


def fetch_files(session: requests.Session, export_id: str, api_version: str) -> List[Dict]:
    url = graph_url(f"{export_id}/files", api_version=api_version)
    return list(paged_get(session, url))


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ExportClientError(f"Unable to create output directory: {path}")


def download_file(download_url: str, dest: Path) -> None:
    with requests.get(download_url, stream=True, timeout=60) as response:  # type: ignore[arg-type]
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ExportClientError(f"Failed to download {download_url}: {exc}") from exc
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)


def download_export_files(
    session: requests.Session,
    export_id: str,
    output_dir: Path,
    api_version: str,
    *,
    rich_console: Optional[Console] = None,
) -> None:
    ensure_output_dir(output_dir)
    files = fetch_files(session, export_id, api_version)
    if not files:
        message = "No files found for export"
        if rich_console:
            rich_console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)
        return

    for file_info in files:
        file_name = file_info.get("file_name") or f"file_{file_info.get('id')}"
        dest = output_dir / file_name
        message = f"Downloading {file_name}..."
        if rich_console:
            rich_console.print(f"[cyan]{message}[/cyan]")
        else:
            print(message)
        download_url = file_info.get("download_url")
        if not download_url:
            warning = f"Skipping {file_name}: missing download URL"
            if rich_console:
                rich_console.print(f"[yellow]{warning}[/yellow]")
            else:
                print(warning, file=sys.stderr)
            continue
        download_file(download_url, dest)
    if rich_console:
        rich_console.print("[green]Download complete[/green]")
    else:
        print("Download complete")


def cmd_community(args: argparse.Namespace) -> None:
    session = build_session(args.token)
    tenant_id = fetch_tenant_id(session, args.api_version)
    print(tenant_id)


def cmd_list_exports(args: argparse.Namespace) -> None:
    session = build_session(args.token)
    exports = fetch_exports(session, args.tenant_id, args.api_version, args.status)
    if not exports:
        return
    for export in exports:
        export_id = export.get("id", "<unknown>")
        status = export.get("status", "")
        created = export.get("created_time", "")
        print(f"{export_id}\t{status}\t{created}")


def cmd_download(args: argparse.Namespace) -> None:
    session = build_session(args.token)
    download_export_files(session, args.export_id, args.output, args.api_version)


def render_exports_table(exports: List[Dict]) -> None:
    if not exports:
        console.print("[yellow]No export jobs were found.[/yellow]")
        return
    table = Table(title="DIY Export Jobs")
    table.add_column("Export ID", style="cyan", overflow="fold")
    table.add_column("Status", style="magenta")
    table.add_column("Created", style="green")
    for export in exports:
        table.add_row(
            str(export.get("id", "")),
            str(export.get("status", "")),
            str(export.get("created_time", "")),
        )
    console.print(table)


def run_wizard(args: argparse.Namespace) -> None:
    console.print(
        Panel(
            "Welcome! This wizard will help you authenticate with the Workplace DIY Export API, "
            "discover your tenant/community ID, list export jobs, and download export files.",
            title="Workplace Export Assistant",
            subtitle="Meta Workplace",
        )
    )

    api_version = Prompt.ask("Graph API version", default=args.api_version)

    token = args.token
    if token:
        console.print("Using access token supplied via command-line or environment variable.")
    else:
        has_token = Confirm.ask(
            "Do you already have a permanent access token for your custom integration?",
            default=True,
        )
        if has_token:
            token = Prompt.ask("Paste your access token", password=True)
        else:
            app_id = Prompt.ask("Enter your custom integration App ID")
            app_secret = Prompt.ask("Enter your App Secret", password=True)
            try:
                token = fetch_app_token(app_id, app_secret, api_version)
                console.print("[green]Access token retrieved successfully.[/green]")
            except ExportClientError as exc:
                console.print(f"[red]{exc}[/red]")
                return

    if not token:
        console.print("[red]An access token is required to continue.[/red]")
        return

    session = build_session(token)

    tenant_id = args.tenant_id or os.getenv("WORKPLACE_TENANT_ID")
    if tenant_id:
        console.print(f"Using tenant/community ID: [bold]{tenant_id}[/bold]")
    else:
        knows_tenant = Confirm.ask(
            "Do you already know your tenant/community ID?",
            default=False,
        )
        if knows_tenant:
            tenant_id = Prompt.ask("Enter your tenant/community ID")
        else:
            try:
                tenant_id = fetch_tenant_id(session, api_version)
                console.print(
                    f"[green]Discovered tenant/community ID:[/green] [bold]{tenant_id}[/bold]"
                )
            except ExportClientError as exc:
                console.print(f"[red]{exc}[/red]")
                tenant_id = Prompt.ask(
                    "Please paste your tenant/community ID (find it in Admin Panel URLs)",
                )
    if not tenant_id:
        console.print("[red]Cannot continue without a tenant/community ID.[/red]")
        return

    while True:
        action = Prompt.ask(
            "What would you like to do next?",
            choices=["list", "download", "quit"],
            default="list",
        )
        if action == "quit":
            console.print("Goodbye! 👋")
            break
        if action == "list":
            status_filter = Prompt.ask(
                "Filter by status (e.g. COMPLETED, RUNNING, leave blank for all)",
                default="COMPLETED",
            )
            status_filter = status_filter.strip().upper()
            if status_filter == "ALL" or status_filter == "":
                status_filter = None
            try:
                exports = fetch_exports(session, tenant_id, api_version, status_filter)
            except ExportClientError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            render_exports_table(exports)
            continue

        export_id = Prompt.ask(
            "Enter the export job ID you would like to download",
        )
        if not export_id:
            console.print("[yellow]No export job ID provided.[/yellow]")
            continue
        output_str = (
            str(args.output)
            if isinstance(args.output, Path)
            else str(args.output) if args.output else "exports"
        )
        output_path_str = Prompt.ask(
            "Where should the files be saved?",
            default=output_str,
        )
        output_dir = Path(output_path_str).expanduser()
        try:
            download_export_files(
                session,
                export_id,
                output_dir,
                api_version,
                rich_console=console,
            )
        except ExportClientError as exc:
            console.print(f"[red]{exc}[/red]")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Workplace DIY Export helper")
    parser.add_argument(
        "--token",
        default=os.getenv("WORKPLACE_TOKEN"),
        help="Access token for the Workplace integration (default: WORKPLACE_TOKEN env)",
    )
    parser.add_argument(
        "--api-version",
        default=DEFAULT_API_VERSION,
        help="Graph API version to use (default: %(default)s)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    community = subparsers.add_parser("community", help="Print the tenant/community ID")
    community.set_defaults(func=cmd_community)

    list_exports = subparsers.add_parser("list", help="List export jobs for a tenant")
    list_exports.add_argument("tenant_id", help="Tenant/community ID")
    list_exports.add_argument(
        "--status",
        help="Filter export jobs by status (e.g. COMPLETED)",
    )
    list_exports.set_defaults(func=cmd_list_exports)

    download = subparsers.add_parser("download", help="Download files for an export")
    download.add_argument("export_id", help="Export job ID")
    download.add_argument(
        "--output",
        type=Path,
        default=Path("exports"),
        help="Destination directory for downloaded files (default: exports/)",
    )
    download.set_defaults(func=cmd_download)

    wizard = subparsers.add_parser("wizard", help="Launch an interactive setup wizard")
    wizard.add_argument(
        "--tenant-id",
        help="Optional tenant/community ID to pre-populate the wizard",
    )
    wizard.add_argument(
        "--output",
        type=Path,
        default=Path("exports"),
        help="Default download directory for the wizard (default: exports/)",
    )
    wizard.set_defaults(func=run_wizard)

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command != "wizard" and not args.token:
        parser.error("Access token must be provided via --token or WORKPLACE_TOKEN")
    return args


def main(argv: Optional[Iterable[str]] = None) -> int:
    try:
        args = parse_args(argv)
        args.func(args)
        return 0
    except ExportClientError as exc:
        console.print(f"[red]{exc}[/red]", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
