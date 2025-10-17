"""Workplace DIY Export downloader."""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

import requests
from requests import Response

API_VERSION = os.getenv("WORKPLACE_GRAPH_API_VERSION", "v20.0")
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
DEFAULT_MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

logger = logging.getLogger("workplace_export")


class ExportDownloadError(RuntimeError):
    """Raised when the export download fails permanently."""


class GraphAPIError(RuntimeError):
    """Raised when the Graph API returns an error payload."""


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download DIY export files from the Workplace Graph API."
    )
    parser.add_argument(
        "--tenant-id",
        dest="tenant_id",
        default=os.getenv("WORKPLACE_TENANT_ID"),
        required=os.getenv("WORKPLACE_TENANT_ID") is None,
        help="Workplace tenant (community) ID.",
    )
    parser.add_argument(
        "--access-token",
        dest="access_token",
        default=os.getenv("WORKPLACE_ACCESS_TOKEN"),
        required=os.getenv("WORKPLACE_ACCESS_TOKEN") is None,
        help="Graph API access token with diy_exports permission.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=os.getenv("WORKPLACE_EXPORT_DIR", "exports"),
        help="Directory where export files will be stored.",
    )
    parser.add_argument(
        "--status",
        dest="status",
        choices=["pending", "in_progress", "completed", "failed"],
        default="completed",
        help="Filter exports by status (default: completed).",
    )
    parser.add_argument(
        "--start-date",
        dest="start_date",
        help="Optional ISO-8601 timestamp used to filter exports created after this date.",
    )
    parser.add_argument(
        "--end-date",
        dest="end_date",
        help="Optional ISO-8601 timestamp used to filter exports created before this date.",
    )
    parser.add_argument(
        "--max-retries",
        dest="max_retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Maximum number of retries for file downloads (default: %(default)s).",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=os.getenv("WORKPLACE_LOG_LEVEL", "INFO"),
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def graph_get(session: requests.Session, url: str, params: Optional[Dict[str, str]] = None) -> Dict:
    response = session.get(url, params=params, timeout=60)
    _raise_for_graph_error(response)
    return response.json()


def _raise_for_graph_error(response: Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            error_payload = response.json().get("error", {})
        except ValueError:
            error_payload = {}
        message = error_payload.get("message", str(exc))
        raise GraphAPIError(message) from exc


def iter_exports(
    session: requests.Session,
    tenant_id: str,
    access_token: str,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Iterable[Dict]:
    params: Dict[str, str] = {"access_token": access_token}
    if status:
        params["status"] = status
    if start_date:
        params["start_time"] = start_date
    if end_date:
        params["end_time"] = end_date

    url = f"{BASE_URL}/{tenant_id}/diy_exports"
    while url:
        logger.debug("Fetching exports from %s", url)
        payload = graph_get(session, url, params=params)
        params = None  # The paging.next URL already contains the params.
        for export in payload.get("data", []):
            yield export
        url = payload.get("paging", {}).get("next")


def list_files(
    session: requests.Session,
    export_id: str,
    access_token: str,
) -> Iterable[Dict]:
    url = f"{BASE_URL}/{export_id}/files"
    params = {"access_token": access_token}
    while url:
        logger.debug("Fetching files for export %s", export_id)
        payload = graph_get(session, url, params=params)
        params = None
        for file_info in payload.get("data", []):
            yield file_info
        url = payload.get("paging", {}).get("next")


def download_file(
    session: requests.Session,
    download_url: str,
    dest_path: Path,
    checksum: Optional[str],
    checksum_algo: str,
    max_retries: int,
) -> None:
    attempt = 0
    while True:
        try:
            logger.info("Downloading %s", download_url)
            with session.get(download_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                hash_obj = hashlib.new(checksum_algo) if checksum else None
                with dest_path.open("wb") as dest_file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            dest_file.write(chunk)
                            if hash_obj:
                                hash_obj.update(chunk)
            if checksum and hash_obj:
                digest = hash_obj.hexdigest()
                if digest.lower() != checksum.lower():
                    raise ExportDownloadError(
                        f"Checksum mismatch for {dest_path}: expected {checksum}, got {digest}"
                    )
            return
        except (requests.RequestException, ExportDownloadError) as exc:
            attempt += 1
            if attempt > max_retries:
                logger.error("Failed to download %s after %s attempts", download_url, attempt)
                raise ExportDownloadError(str(exc)) from exc
            sleep_seconds = RETRY_BACKOFF_SECONDS * attempt
            logger.warning(
                "Download error (%s). Retrying in %s seconds (attempt %s/%s)",
                exc,
                sleep_seconds,
                attempt,
                max_retries,
            )
            time.sleep(sleep_seconds)


def process_exports(args: argparse.Namespace) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "MetaWorkplaceExport/1.0"})

    tenant_output = Path(args.output_dir)
    tenant_output.mkdir(parents=True, exist_ok=True)

    for export in iter_exports(
        session,
        tenant_id=args.tenant_id,
        access_token=args.access_token,
        status=args.status,
        start_date=args.start_date,
        end_date=args.end_date,
    ):
        status = export.get("status")
        export_id = export.get("id")
        if status != "completed":
            logger.debug("Skipping export %s with status %s", export_id, status)
            continue

        export_dir = tenant_output / export_id
        export_dir.mkdir(parents=True, exist_ok=True)

        for file_info in list_files(session, export_id=export_id, access_token=args.access_token):
            file_name = file_info.get("file_name") or f"{file_info.get('id', 'file')}"
            download_url = file_info.get("download_url")
            checksum = file_info.get("checksum") or file_info.get("sha256")
            checksum_algo = "sha256"
            if not download_url:
                logger.warning("Skipping file %s without download_url", file_name)
                continue

            destination = export_dir / file_name
            if destination.exists():
                logger.info("File %s already exists, skipping", destination)
                continue

            download_file(
                session,
                download_url=download_url,
                dest_path=destination,
                checksum=checksum,
                checksum_algo=checksum_algo,
                max_retries=args.max_retries,
            )

            logger.info("Saved %s", destination)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    try:
        process_exports(args)
    except (GraphAPIError, ExportDownloadError, requests.RequestException) as exc:
        logger.error("Export failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
