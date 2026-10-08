# qdrant-full-mcp

One MCP server for **all of Qdrant**:

- **`cloud` module**: Qdrant Cloud *account* management via the official Cloud API
  (clusters, backups, schedules, database API keys, members).
- **`data` module**: database *data plane* (collections, points, vector search) via the Qdrant REST API.

Works with Claude Code and any MCP client (stdio).

## Tools

**Cloud (read-only):** `list_accounts`, `list_clusters`, `get_cluster`, `list_cloud_providers`, `list_regions`, `list_packages`,
`list_releases`, `list_backups`, `list_backup_schedules`, `list_database_api_keys`, `list_members`

**Cloud (changes things):** `create_cluster`, `update_cluster`, `delete_cluster`, `restart_cluster`,
`suspend_cluster`, `unsuspend_cluster`, `create_backup`, `delete_backup`, `restore_backup`,
`create_backup_schedule`, `delete_backup_schedule`, `create_database_api_key`, `delete_database_api_key`

**Data (read-only):** `list_collections`, `get_collection`, `count_points`, `get_points`, `scroll_points`,
`query_points` (takes a ready-made vector; no embedding is done here)

**Data (changes things):** `create_collection`, `delete_collection`, `upsert_points`, `delete_points`

### Safety model
- Every mutating tool is a **dry run unless `confirm=true`**; the tool descriptions tell the model to get your
  approval first.
- Irreversible tools need the exact name: `delete_cluster` / `restore_backup` (`confirm_cluster_name`),
  `delete_collection` (`confirm_collection_name`).
- `QDRANT_MCP_READ_ONLY=1` blocks all confirmed writes. `QDRANT_MCP_MODULES=cloud` or `=data` loads one module.
- Keys come from environment variables only and are never logged or echoed. `create_database_api_key` returns
  the new secret once (Qdrant never shows it again).
- Paths and ids are validated before any request. Prefer least-privilege keys.

## Configuration

| Variable | Used by | Notes |
|---|---|---|
| `QDRANT_CLOUD_API_KEY` | cloud | Console -> Access Management -> Cloud Management Keys |
| `QDRANT_CLOUD_ACCOUNT_ID` | cloud | optional: auto-detected from the key when it has exactly one account; set it if you have several |
| `QDRANT_URL` | data | not needed if you pass `cluster_id`; e.g. `https://<id>.<region>.cloud.qdrant.io:6333`; `get_cluster` shows the endpoint |
| `QDRANT_API_KEY` | data | database API key (not the management key); optional for local Qdrant |
| `QDRANT_API_KEY_<CLUSTER_ID>` | data | per-cluster key, UUID uppercased with `_` for `-`; wins over `QDRANT_API_KEY` |
| `QDRANT_MCP_MODULES` | both | `cloud,data` (default) |
| `QDRANT_MCP_READ_ONLY` | both | `1` to block writes |

See `.env.example`. Never commit real values.

### Targeting a Cloud cluster (v0.2)
Every data tool takes an optional `cluster_id`. The endpoint is looked up through the Cloud API, so you do not set
`QDRANT_URL`; you only provide the database key (`QDRANT_API_KEY_<ID>` or `QDRANT_API_KEY`). Create one with
`create_database_api_key`. The management key itself never touches data.

## Quick start
Only one variable is required: `QDRANT_CLOUD_API_KEY` (a Cloud Management Key). Everything else is optional.

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
claude mcp add qdrant --scope user \
  -e 'QDRANT_CLOUD_API_KEY=${QDRANT_CLOUD_API_KEY}' \
  -e 'QDRANT_CLOUD_ACCOUNT_ID=${QDRANT_CLOUD_ACCOUNT_ID}' \
  -e 'QDRANT_URL=${QDRANT_URL}' \
  -e 'QDRANT_API_KEY=${QDRANT_API_KEY}' \
  -- uvx qdrant-full-mcp
```

(Before it is on PyPI: `-- uvx --from git+https://github.com/<you>/qdrant-full-mcp qdrant-full-mcp`,
or `-- uv run --project <path-to-clone> qdrant-full-mcp`.)

Verify: `claude mcp list`, or `/mcp` inside Claude Code.

## Example prompts
- "List my Qdrant Cloud clusters."
- "Show the available AWS regions."
- "Create a Qdrant cluster in AWS London, 1 node, smallest package." (Claude shows the cost, waits for approval.)
- "Back up cluster X and keep it for 30 days."
- "Create a read-only database API key for cluster X that expires in 90 days."
- "How many points are in collection `docs`?"
- "Delete cluster `old-test`." (you must confirm the exact name)

## Development

```bash
pip install -e ".[dev]"
pytest
```

Cloud API schemas: https://github.com/qdrant/qdrant-cloud-public-api. Note: the Cloud REST gateway expects
snake_case query parameters (e.g. `page_size`) and accepts camelCase JSON bodies.

## Status
Read-only cloud tools and auth are verified against the live API. Write tools are covered by mocked tests but
were not exercised against a real account; test on a throwaway cluster first.

## License
MIT
