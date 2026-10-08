# qdrant-full-mcp

**One MCP server for all of Qdrant.** Manage your Qdrant Cloud account (clusters, backups, API keys) *and* work with
your data (collections, points, vector and hybrid search) from Claude Code, Codex, Cursor, VS Code or any MCP client.

- **Cloud management:** create, scale, suspend, restart, back up, restore and delete clusters through the official Qdrant Cloud API.
- **Data plane:** create collections, upsert and search points, hybrid dense + sparse search, payload indexes.
- **Safe by default:** every change is a dry run until confirmed; destructive actions need the exact resource name.
- **One variable to start:** just your Cloud Management Key. The account ID is detected automatically.

## Quick start

**1. Get a key.** Qdrant Cloud Console -> *Access Management* -> *Cloud Management Keys* -> create one.
(Least privilege is fine: read-only permissions are enough for the read tools.)

**2. Install.** Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/). Pick your client:

### Claude Code

```bash
claude mcp add qdrant -e QDRANT_CLOUD_API_KEY=your-management-key -- uvx --from git+https://github.com/Jeevav62/qdrant-full-mcp qdrant-full-mcp
```

Check with `claude mcp list`, or `/mcp` inside Claude Code. Then try: *"List my Qdrant Cloud clusters."*

Prefer not to store the key in Claude's config? Set `QDRANT_CLOUD_API_KEY` in your shell profile and use
`-e 'QDRANT_CLOUD_API_KEY=${QDRANT_CLOUD_API_KEY}'` instead.

**As a plugin** (adds a usage skill that teaches Claude the safe workflows):

```
/plugin marketplace add Jeevav62/qdrant-full-mcp
/plugin install qdrant@qdrant-full-mcp
```

With the plugin, set `QDRANT_CLOUD_API_KEY` in your environment before starting Claude Code.

### Codex

```bash
codex mcp add qdrant --env QDRANT_CLOUD_API_KEY=your-management-key -- uvx --from git+https://github.com/Jeevav62/qdrant-full-mcp qdrant-full-mcp
```

Or edit `~/.codex/config.toml` by hand:

```toml
[mcp_servers.qdrant]
command = "uvx"
args = ["--from", "git+https://github.com/Jeevav62/qdrant-full-mcp", "qdrant-full-mcp"]

[mcp_servers.qdrant.env]
QDRANT_CLOUD_API_KEY = "your-management-key"
```

Verify with `codex mcp list`.

### Cursor, Claude Desktop, Windsurf and other JSON-config clients

```json
{
  "mcpServers": {
    "qdrant": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Jeevav62/qdrant-full-mcp", "qdrant-full-mcp"],
      "env": { "QDRANT_CLOUD_API_KEY": "your-management-key" }
    }
  }
}
```

(Cursor: `~/.cursor/mcp.json`. Claude Desktop: `claude_desktop_config.json`.)

### VS Code

`.vscode/mcp.json`:

```json
{
  "servers": {
    "qdrant": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Jeevav62/qdrant-full-mcp", "qdrant-full-mcp"],
      "env": { "QDRANT_CLOUD_API_KEY": "your-management-key" }
    }
  }
}
```

### From a local clone

```bash
git clone https://github.com/Jeevav62/qdrant-full-mcp
claude mcp add qdrant -e QDRANT_CLOUD_API_KEY=your-management-key -- uv run --project ./qdrant-full-mcp qdrant-full-mcp
```

## Using the data tools

The management key manages infrastructure only; it cannot read or write vectors. For data you need a *database API key*
for the cluster, which you can create by asking: *"Create a read-write database API key for cluster X."* Then:

```bash
export QDRANT_API_KEY=the-database-key                 # one cluster
export QDRANT_API_KEY_<CLUSTER_ID>=the-database-key    # several (UUID uppercased, '-' -> '_')
```

Pass `cluster_id` to any data tool and the endpoint is looked up for you. For a local or self-hosted Qdrant, set
`QDRANT_URL` (e.g. `http://localhost:6333`) and `QDRANT_API_KEY` instead and skip `cluster_id`.

## Example prompts

- "List my Qdrant Cloud clusters." / "Show the available AWS regions."
- "Create a Qdrant cluster in AWS London with the smallest paid package." (shows the price first, waits for your approval)
- "Scale cluster X to 3 nodes." / "Suspend cluster X."
- "Back up cluster X every day at 3am and keep backups for 14 days."
- "Restore backup Y into a new cluster called `restored-1`."
- "Create a read-only database API key for cluster X that expires in 90 days."
- "Create a collection `docs` with a dense vector (768, Cosine) and a sparse vector for hybrid search."
- "Add a keyword index on `tenant_id` in `docs`." / "How many points are in `docs`?"
- "Delete cluster `old-test`." (you must confirm the exact name)

## Tools

**Cloud, read-only:** `list_accounts`, `list_clusters`, `get_cluster`, `list_cloud_providers`, `list_regions`,
`list_packages`, `list_releases`, `list_backups`, `list_backup_schedules`, `list_database_api_keys`, `list_members`

**Cloud, changes things:** `create_cluster`, `update_cluster`, `delete_cluster`, `restart_cluster`, `suspend_cluster`,
`unsuspend_cluster`, `create_backup`, `delete_backup`, `restore_backup_to_new_cluster`, `restore_backup` (in place,
overwrites), `create_backup_schedule`, `delete_backup_schedule`, `create_database_api_key`, `delete_database_api_key`

**Data, read-only:** `list_collections`, `get_collection`, `list_payload_indexes`, `count_points`, `get_points`,
`scroll_points`, `query_points` (dense or sparse), `hybrid_query_points` (dense + sparse, RRF or DBSF fusion)

**Data, changes things:** `create_collection` (single, named and sparse vectors), `delete_collection`,
`create_payload_index`, `delete_payload_index`, `upsert_points`, `delete_points`

## Safety

- Every mutating tool is a **dry run unless `confirm=true`**, and the tool descriptions tell the model to get your approval first.
- Irreversible tools need the exact name: `delete_cluster` and `restore_backup` (`confirm_cluster_name`), `delete_collection` (`confirm_collection_name`).
- `QDRANT_MCP_READ_ONLY=1` blocks every confirmed write. `QDRANT_MCP_MODULES=cloud` or `=data` loads one module only.
- Keys come from environment variables only and are never logged or echoed. A new database key's secret is shown once by Qdrant.
- Ids and paths are validated before any request. Prefer least-privilege keys.

## Configuration

| Variable | Needed for | Notes |
|---|---|---|
| `QDRANT_CLOUD_API_KEY` | cloud tools | the only required variable |
| `QDRANT_CLOUD_ACCOUNT_ID` | optional | auto-detected when the key has one account; set it if you have several |
| `QDRANT_API_KEY` | data tools | database API key (optional for unauthenticated local Qdrant) |
| `QDRANT_API_KEY_<CLUSTER_ID>` | data tools | per-cluster key, wins over `QDRANT_API_KEY` |
| `QDRANT_URL` | data tools | only without `cluster_id`, e.g. `http://localhost:6333` |
| `QDRANT_MCP_MODULES` | optional | `cloud,data` (default) |
| `QDRANT_MCP_READ_ONLY` | optional | `1` blocks writes |
| `QDRANT_CLOUD_API_URL` | optional | default `https://api.cloud.qdrant.io` |

See `.env.example`. Never commit real values.

## Status and roadmap

Verified against the live Qdrant Cloud API on a free-tier cluster: cluster create and update, restart, suspend and
unsuspend, database API keys, and the data tools (collections, named and sparse vectors, upsert, dense, sparse and
hybrid search, payload indexes, deletes).

Not verified live yet: backups, backup schedules and restores (free-tier clusters do not support them), and cluster
delete. Test those on a throwaway paid cluster first.

Ideas for later: snapshots, named-vector search options (quantization, HNSW tuning), payload updates without
re-upserting, collection aliases, cluster configuration settings, a PyPI release (`uvx qdrant-full-mcp`).

**Not covered by design:** inviting or removing members, roles and billing; hybrid-cloud environments and serverless
spaces; text-to-vector embedding (bring your own vectors).

## Development

```bash
pip install -e ".[dev]"
pytest
```

Cloud API schemas: https://github.com/qdrant/qdrant-cloud-public-api. The Cloud REST gateway expects snake_case query
parameters (e.g. `page_size`) and accepts camelCase JSON bodies.

## License

MIT
