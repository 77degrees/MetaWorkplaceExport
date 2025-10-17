# Workplace DIY Export Helper

This repository contains a Python assistant that helps Workplace administrators authenticate, discover their tenant/community ID, list completed DIY export jobs, and download the archive files.

## Requirements
- Python 3.9+
- `requests` and `rich` Python packages (`pip install -r requirements.txt`)
- A Workplace custom integration with the **Read Workplace company data exports** permission

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/workplace_export.py wizard
```

The wizard will:
1. Prompt for your permanent access token or help you exchange an App ID/Secret for a token.
2. Discover (or let you paste) your tenant/community ID.
3. List export jobs with a Rich-powered table UI.
4. Download the files for any export job you select.

You can pass known values ahead of time:

```bash
export WORKPLACE_TOKEN="<permanent_token>"
python scripts/workplace_export.py wizard --tenant-id <COMMUNITY_ID>
```

## CLI commands
If you prefer direct commands, the underlying CLI is still available:

### Discover your community (tenant) ID
```bash
python scripts/workplace_export.py community --token "$WORKPLACE_TOKEN"
```

### List export jobs for a tenant
```bash
python scripts/workplace_export.py list <TENANT_ID> --status COMPLETED --token "$WORKPLACE_TOKEN"
```

### Download files for an export job
```bash
python scripts/workplace_export.py download <EXPORT_ID> --output /path/to/archive --token "$WORKPLACE_TOKEN"
```

The `download` command saves every file associated with the export job to the specified directory (defaults to `./exports`). URLs returned by the API are short-lived, so run the download soon after listing the files.

## Notes
- Use the `--api-version` option (on any command or the wizard) if your Workplace instance requires a different Graph API version.
- Always handle export data securely and follow your organization’s data retention policies.
- Tokens and secrets are sensitive—store them in a credential vault rather than plaintext files.
