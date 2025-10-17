# MetaWorkplaceExport

This repository provides a small utility for downloading files produced by the Workplace DIY Export API. The `scripts/export.py` module authenticates with the Graph API, lists completed exports, and saves the associated export files to disk with checksum verification and retry-aware downloads.

## Prerequisites

* Python 3.9+
* [requests](https://docs.python-requests.org/en/latest/) (install with `pip install -r requirements.txt` or `pip install requests`)

## Configuration

The CLI accepts explicit flags or reads from environment variables so that credentials can be supplied securely:

| Environment variable | CLI flag | Description |
| --- | --- | --- |
| `WORKPLACE_TENANT_ID` | `--tenant-id` | Workplace community (tenant) ID. |
| `WORKPLACE_ACCESS_TOKEN` | `--access-token` | Graph API access token with permissions for DIY exports. |
| `WORKPLACE_EXPORT_DIR` | `--output-dir` | Target directory for downloaded files. |
| `WORKPLACE_GRAPH_API_VERSION` | _n/a_ | Override the Graph API version (default `v20.0`). |
| `WORKPLACE_LOG_LEVEL` | `--log-level` | Logging verbosity (default `INFO`). |

## Usage

```bash
python scripts/export.py \
    --tenant-id "<tenant id>" \
    --access-token "$WORKPLACE_ACCESS_TOKEN" \
    --output-dir ./exports \
    --start-date "2024-01-01T00:00:00Z" \
    --end-date "2024-02-01T00:00:00Z" \
    --status completed
```

By default the script only processes exports that report a `completed` status. File downloads include checksum validation (based on the `checksum`/`sha256` metadata supplied by the API) and automatic retry logic for transient network errors.

### Filters

Optional filters can be supplied with `--start-date`, `--end-date`, and `--status` (one of `pending`, `in_progress`, `completed`, `failed`). These parameters are forwarded to the `/diy_exports` endpoint so that only the relevant export jobs are iterated.

## Scheduling automated runs

Because credentials can be expressed as environment variables, the downloader is straightforward to automate. For example, the following cron entry runs the exporter every night at 1 AM and writes logs to `/var/log/workplace-export.log`:

```
0 1 * * * WORKPLACE_TENANT_ID=12345 \
WORKPLACE_ACCESS_TOKEN="$(cat /etc/workplace/token)" \
WORKPLACE_EXPORT_DIR=/mnt/exports \
/usr/bin/python3 /opt/MetaWorkplaceExport/scripts/export.py >> /var/log/workplace-export.log 2>&1
```

For containerized or CI environments, inject the environment variables using the platform's secret management features and invoke `python scripts/export.py` as part of the scheduled workflow.

## Development

Clone the repository and run the exporter locally:

```bash
git clone https://github.com/your-org/MetaWorkplaceExport.git
cd MetaWorkplaceExport
python -m venv .venv
source .venv/bin/activate
pip install requests
python scripts/export.py --help
```

The `--help` flag describes all available CLI options.
