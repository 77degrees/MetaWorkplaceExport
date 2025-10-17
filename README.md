# Workplace DIY Export Helper

This repository contains a Python assistant that helps Workplace administrators authenticate, discover their tenant/community ID, list completed DIY export jobs, and download the archive files. It wraps the flows documented in the [Workplace DIY Export API reference](https://developers.facebook.com/docs/workplace/reference/graph-api/dyi-export) so you can run them from an interactive terminal instead of crafting raw Graph API requests. The helper mirrors the documentation’s sample `requests` snippet by calling `/community/work_dyi_jobs`, `/{export_id}?fields=…`, `/{export_id}/user_dyi_jobs`, and `/{job_id}/files` on your behalf.

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

The wizard will (following the steps recommended in the [DIY Export API guide](https://developers.facebook.com/docs/workplace/reference/graph-api/dyi-export)):
1. Prompt for your permanent access token or help you exchange an App ID/Secret for a token.
2. Discover (or let you paste) your tenant/community ID and, if it’s unavailable, explain how to rely on the `/community/work_dyi_jobs`
   endpoint instead.
3. List export jobs with a Rich-powered table UI, including the `is_completed`, `company_job`, and timestamp details from the documentation.
4. Display an export summary panel (status, DIY types, completed sub-jobs) before any downloads begin.
5. Download the files for the selected export, iterating over both the company job and each user DIY job just like the official sample code.

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

### List export jobs for a tenant or via `/community/work_dyi_jobs`
```bash
python scripts/workplace_export.py list <TENANT_ID> --status COMPLETED --token "$WORKPLACE_TOKEN"

# Or rely on the `/community/work_dyi_jobs` endpoint showcased in the documentation
python scripts/workplace_export.py list --status COMPLETED --token "$WORKPLACE_TOKEN"
```

### Download files for an export job
```bash
python scripts/workplace_export.py download <EXPORT_ID> --output /path/to/archive --token "$WORKPLACE_TOKEN"
```

The `download` command saves every file associated with the export job (company and user DIY jobs) to the specified directory (defaults to `./exports`). URLs returned by the API are short-lived, so run the download soon after listing the files.

## Notes
- Review the official [DIY Export API reference](https://developers.facebook.com/docs/workplace/reference/graph-api/dyi-export) for the full schema, status lifecycle, and permissions required for each endpoint. This CLI simply orchestrates those documented calls.
- Use the `--api-version` option (on any command or the wizard) if your Workplace instance requires a different Graph API version.
- Always handle export data securely and follow your organization’s data retention policies.
- Tokens and secrets are sensitive—store them in a credential vault rather than plaintext files.
