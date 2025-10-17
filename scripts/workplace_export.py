"""Utility CLI and interactive wizard for the Workplace DIY Export API.

The helper wraps the endpoints documented at
https://developers.facebook.com/docs/workplace/reference/graph-api/dyi-export
so administrators can follow the official flow without manually crafting HTTP
requests.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

DEFAULT_API_VERSION = "v17.0"
GRAPH_BASE = "https://graph.facebook.com/{version}/{path}"
DIY_EXPORT_DOC = "https://developers.facebook.com/docs/workplace/reference/graph-api/dyi-export"

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
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, str]] = None,
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


def fetch_work_dyi_jobs(
    session: requests.Session,
    api_version: str,
    *,
    only_completed: bool = False,
) -> List[Dict]:
    """Return community DIY export jobs using the documented /community endpoint."""
    url = graph_url("community/work_dyi_jobs", api_version=api_version)
    jobs = list(paged_get(session, url))
    if only_completed:
        jobs = [job for job in jobs if job.get("is_completed")]
    return jobs


def fetch_tenant_exports(
    session: requests.Session,
    tenant_id: str,
    api_version: str,
    status: Optional[str] = None,
) -> List[Dict]:
    """Return DIY exports for a specific tenant/community ID."""
    url = graph_url(f"{tenant_id}/diy_exports", api_version=api_version)
    params: Dict[str, str] = {}
    if status:
        params["status"] = status
    return list(paged_get(session, url, params=params))


def fetch_files(session: requests.Session, export_id: str, api_version: str) -> List[Dict]:
    url = graph_url(f"{export_id}/files", api_version=api_version)
    return list(paged_get(session, url))


def fetch_export_job(
    session: requests.Session,
    export_id: str,
    api_version: str,
    *,
    extra_fields: Optional[Sequence[str]] = None,
) -> Dict:
    fields = ["id", "is_completed", "created_time", "company_job"]
    if extra_fields:
        fields.extend(extra_fields)
    url = graph_url(export_id, api_version=api_version)
    response = session.get(url, params={"fields": ",".join(fields)})
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ExportClientError(f"Failed to fetch export job {export_id}.") from exc
    return response.json()


def fetch_user_jobs(session: requests.Session, export_id: str, api_version: str) -> List[Dict]:
    url = graph_url(f"{export_id}/user_dyi_jobs", api_version=api_version)
    return list(paged_get(session, url))


def prompt_sensitive(
    prompt_message: str,
    *,
    secret_name: str,
    default_hide: bool = True,
) -> str:
    """Prompt the user for sensitive input while letting them decide whether to mask it."""

    hide_input = Confirm.ask(
        f"Hide the {secret_name} while typing/pasting?",
        default=default_hide,
    )
    return Prompt.ask(prompt_message, password=hide_input)


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
    include_user_jobs: bool = True,
) -> None:
    ensure_output_dir(output_dir)
    export_job = fetch_export_job(session, export_id, api_version)
    if not export_job.get("is_completed"):
        message = "Export job is not marked complete yet. Some files may be missing."
        if rich_console:
            rich_console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)

    download_targets: List[Tuple[str, Dict]] = []

    company_job = (export_job.get("company_job") or {})
    if company_job.get("id"):
        download_targets.append(("Company", company_job))

    if include_user_jobs:
        user_jobs = fetch_user_jobs(session, export_id, api_version)
        for job in user_jobs:
            if job.get("id"):
                download_targets.append(("User", job))

    if not download_targets:
        message = "No company or user DIY jobs were found for this export."
        if rich_console:
            rich_console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)
        return

    for job_type, job_info in download_targets:
        files = fetch_files(session, job_info["id"], api_version)
        if not files:
            message = f"No files found for {job_type.lower()} job {job_info['id']}"
            if rich_console:
                rich_console.print(f"[yellow]{message}[/yellow]")
            else:
                print(message, file=sys.stderr)
            continue

        section_title = f"{job_type} job {job_info['id']}"
        if rich_console:
            console_message = f"[bold]{section_title}[/bold]"
            rich_console.print(console_message)
        else:
            print(section_title)

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
    if args.tenant_id:
        exports = fetch_tenant_exports(session, args.tenant_id, args.api_version, args.status)
    else:
        only_completed = (args.status or "").upper() == "COMPLETED"
        exports = fetch_work_dyi_jobs(
            session,
            args.api_version,
            only_completed=only_completed,
        )
        if args.status and args.status.upper() not in {"", "COMPLETED"}:
            exports = [
                job
                for job in exports
                if str(job.get("status", "")).upper() == args.status.upper()
            ]
    if not exports:
        return
    for export in exports:
        export_id = export.get("id", "<unknown>")
        status = export.get("status")
        if status is None and export.get("is_completed") is not None:
            status = "COMPLETED" if export.get("is_completed") else "IN_PROGRESS"
        created = export.get("created_time", "")
        print(f"{export_id}\t{status or ''}\t{created}")


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
    table.add_column("Completed?", style="green")
    table.add_column("Created", style="blue")
    for export in exports:
        status_value = export.get("status")
        if status_value is None and export.get("is_completed") is not None:
            status_value = "COMPLETED" if export.get("is_completed") else "IN_PROGRESS"
        table.add_row(
            str(export.get("id", "")),
            str(status_value or ""),
            "Yes" if export.get("is_completed") else "No",
            str(export.get("created_time", "")),
        )
    console.print(table)


def run_wizard(args: argparse.Namespace) -> None:
    console.print(
        Panel(
            "Welcome! This wizard will help you authenticate with the Workplace DIY Export API, "
            "discover your tenant/community ID, list export jobs, and download export files.\n\n"
            "It follows the steps outlined in the official documentation ("
            f"[link={DIY_EXPORT_DOC}]DIY Export API reference[/link]) so you can reproduce"
            " the recommended workflow programmatically.",
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
            token = prompt_sensitive(
                "Paste your access token",
                secret_name="access token",
                default_hide=False,
            )
        else:
            app_id = Prompt.ask("Enter your custom integration App ID")
            app_secret = prompt_sensitive(
                "Enter your App Secret",
                secret_name="App Secret",
            )
            try:
                token = fetch_app_token(app_id, app_secret, api_version)
                console.print("[green]Access token retrieved successfully.[/green]")
            except ExportClientError as exc:
                console.print(f"[red]{exc}[/red]")
                return

    if not token:
        console.print("[red]An access token is required to continue.[/red]")
        return

    console.print(
        "[dim]Credentials are only held in memory for this session; they are never "
        "written to disk by this tool.[/dim]"
    )

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
        console.print(
            "[yellow]No tenant/community ID stored. We'll use the documented "
            "`/community/work_dyi_jobs` endpoint (from the DIY Export guide) to look "
            "up jobs for this community.[/yellow]"
        )

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
                if tenant_id:
                    exports = fetch_tenant_exports(
                        session,
                        tenant_id,
                        api_version,
                        status_filter,
                    )
                else:
                    exports = fetch_work_dyi_jobs(
                        session,
                        api_version,
                        only_completed=(status_filter or "") == "COMPLETED",
                    )
                    if status_filter and status_filter not in {"COMPLETED"}:
                        exports = [
                            job
                            for job in exports
                            if str(job.get("status", "")).upper() == status_filter
                        ]
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

        try:
            job_details = fetch_export_job(
                session,
                export_id,
                api_version,
                extra_fields=["status", "diy_types", "total_number_of_completed_jobs"],
            )
        except ExportClientError as exc:
            console.print(f"[red]{exc}[/red]")
            continue

        summary_lines = [
            f"ID: {job_details.get('id', export_id)}",
            f"Status: {job_details.get('status', 'unknown')}",
            f"Completed: {bool(job_details.get('is_completed'))}",
        ]
        diy_types = job_details.get("diy_types")
        if diy_types:
            summary_lines.append(f"DIY types: {', '.join(diy_types)}")
        total_completed = job_details.get("total_number_of_completed_jobs")
        if total_completed is not None:
            summary_lines.append(f"Completed sub-jobs: {total_completed}")
        console.print(Panel("\n".join(summary_lines), title="Export job summary"))

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

    list_exports = subparsers.add_parser(
        "list",
        help="List export jobs (defaults to /community/work_dyi_jobs unless a tenant ID is provided)",
    )
    list_exports.add_argument(
        "tenant_id",
        nargs="?",
        help="Optional tenant/community ID; if omitted the /community/work_dyi_jobs endpoint is used",
    )
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
