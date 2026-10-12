# qdrant-full-mcp

**One MCP server for all of Qdrant.** Manage your Qdrant Cloud account (clusters, backups, API keys) *and* work with
your data (collections, points, vector and hybrid search) from Claude Code, Codex, Cursor, VS Code or any MCP client.
Everything the official `mcp-server-qdrant` does (store and find text) is included; see the [comparison](#compared-with-the-official-mcp-server-qdrant).

> Unofficial community project, not affiliated with or endorsed by Qdrant.

- **Cloud management:** create, scale, suspend, restart, back up, restore and delete clusters through the official Qdrant Cloud API.
- **Data plane:** create collections, upsert and search points, hybrid dense + sparse search, payload indexes.
- **Plain-text memory:** `store_text` and `find_text` embed text locally (FastEmbed), so you can store and search
  without making vectors yourself. Works with collections made by the official `mcp-server-qdrant`.
- **Safe by default:** every change is a dry run until confirmed; destructive actions need the exact resource name.
- **One variable to start:** just your Cloud Management Key. The account ID is detected automatically, and the agent
  creates and remembers the database key itself, so you can go from no cluster to searching your data in one chat.

## Quick start

**1. Get a key.** Qdrant Cloud Console -> *Access Management* -> *Cloud Management Keys* -> create one.
(Least privilege is fine: read-only permissions are enough for the read tools.)

**2. Download once.** Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/). The first run fetches
the dependencies (about 40 seconds, cached afterwards), which is longer than some clients wait for a server to start:

```bash
uvx qdrant-full-mcp --help
```

**3. Add it to your client:**

### Claude Code

```bash
claude mcp add qdrant -e QDRANT_CLOUD_API_KEY=your-management-key -- uvx qdrant-full-mcp
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
codex mcp add qdrant --env QDRANT_CLOUD_API_KEY=your-management-key -- uvx qdrant-full-mcp
```

Or edit `~/.codex/config.toml` by hand:

```toml
[mcp_servers.qdrant]
command = "uvx"
args = ["qdrant-full-mcp"]
startup_timeout_sec = 60

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
      "args": ["qdrant-full-mcp"],
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
      "args": ["qdrant-full-mcp"],
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

The management key manages infrastructure only; Qdrant does not let it read or write vectors. Data needs a *database
API key*, and the agent can make one for you, with no config editing and no restart:

> *"Create a free cluster in AWS Mumbai, then make me a database key and store these notes in it."*

When `create_database_api_key` creates a key, the server remembers it for that cluster and the data tools use it at
once. The agent asks whether to **save it on this machine**; if you say yes, every later session (Claude Code, Codex,
Cursor, ...) uses it automatically. Saved keys live in `%APPDATA%\qdrant-full-mcp\keys.json` on Windows or
`~/.config/qdrant-full-mcp/keys.json` elsewhere (file mode 600, never printed by any tool). Deleting the key or the
cluster removes it from there too; `forget_database_api_key` removes it on request, `list_remembered_keys` shows what
is stored without secrets. Set `QDRANT_MCP_KEY_FILE=off` to never write keys to disk.

You can still set keys yourself; they take priority over remembered ones:

```bash
export QDRANT_API_KEY_<CLUSTER_ID>=the-database-key    # per cluster (UUID uppercased, '-' -> '_')
export QDRANT_API_KEY=the-database-key                 # fallback for any cluster
```

Pass `cluster_id` to any data tool and the endpoint is looked up for you. For a local or self-hosted Qdrant, set
`QDRANT_URL` (e.g. `http://localhost:6333`) and `QDRANT_API_KEY` instead and skip `cluster_id`.

## Store and search plain text

`store_text` and `find_text` embed text on your machine with [FastEmbed](https://github.com/qdrant/fastembed), the same
approach as the official `mcp-server-qdrant`, so no vectors are needed. Nothing extra to install.

For a simple agent memory, set a default collection:

```bash
claude mcp add qdrant -e QDRANT_URL=https://your-cluster:6333 -e QDRANT_API_KEY=db-key -e COLLECTION_NAME=memory   -- uvx qdrant-full-mcp
```

Then just say *"Remember that refunds take 5 business days"* and later *"How long do refunds take?"*. With
`COLLECTION_NAME` set, storing into that collection happens in one step and the collection is created on first use;
any other collection keeps the dry-run-then-confirm flow. Without it, name the collection in your request.

- **No server at all:** set `QDRANT_LOCAL_PATH=./qdrant-data` instead of `QDRANT_URL` and Qdrant runs inside the MCP
  server, storing data in that folder (`:memory:` keeps it in RAM). Every data and text tool works the same. Good for
  learning and small projects; switch to a real cluster later by changing one variable.
- `TOOL_STORE_DESCRIPTION` and `TOOL_FIND_DESCRIPTION` replace the tool descriptions, e.g. *"Store a reusable code
  snippet with its description"*, to turn the memory into something specific. `QDRANT_SEARCH_LIMIT` sets the default
  number of results (5).
- The first text call downloads the model (about 90 MB, cached afterwards).
- Filtering on Qdrant Cloud: Cloud clusters require a payload index on any field you filter by. Ask *"add a keyword
  index on metadata.topic in memory"* once; if you forget, the error tells the model the exact call that fixes it.
- Default model `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions); change it with `EMBEDDING_MODEL`.
- Layout matches the official server: a named vector `fast-<model>` and payload `{"document", "metadata"}`, so both
  servers can read and write the same collection.

## Remote (HTTP) mode

stdio is the default. To run it as a server that clients connect to over HTTP:

```bash
qdrant-full-mcp --transport streamable-http --port 8000   # endpoint http://127.0.0.1:8000/mcp
qdrant-full-mcp --transport sse                           # older SSE clients
```

It binds to `127.0.0.1` by default. There is no built-in authentication and the server holds your Qdrant keys, so if
you bind to another address (`--host 0.0.0.0`), put an authenticating proxy in front of it.

With Docker (the image includes the default embedding model and serves streamable HTTP on port 8000):

```bash
docker build -t qdrant-full-mcp .
docker run -p 127.0.0.1:8000:8000 -e QDRANT_URL=https://your-cluster:6333 -e QDRANT_API_KEY=db-key   -e COLLECTION_NAME=memory qdrant-full-mcp
```

## Example prompts

- "List my Qdrant Cloud clusters." / "Show the available AWS regions."
- "Create a Qdrant cluster in AWS London with the smallest paid package." (shows the price first, waits for your approval)
- "Scale cluster X to 3 nodes." / "Suspend cluster X."
- "Back up cluster X every day at 3am and keep backups for 14 days."
- "Restore backup Y into a new cluster called `restored-1`."
- "Create a read-only database API key for cluster X that expires in 90 days."
- "Make a database key for cluster X and save it on this machine." (data tools then work in every session)
- "Create a collection `docs` with a dense vector (768, Cosine) and a sparse vector for hybrid search."
- "Add a keyword index on `tenant_id` in `docs`." / "How many points are in `docs`?"
- "Store these three FAQ answers in collection `faq`." / "Search `faq` for how refunds work."
- "Delete cluster `old-test`." (you must confirm the exact name)

## Tools

**Cloud, read-only:** `list_accounts`, `list_clusters`, `get_cluster`, `list_cloud_providers`, `list_regions`,
`list_packages`, `list_releases`, `list_backups`, `list_backup_schedules`, `list_database_api_keys`, `list_members`

**Cloud, changes things:** `create_cluster`, `update_cluster`, `delete_cluster`, `restart_cluster`, `suspend_cluster`,
`unsuspend_cluster`, `create_backup`, `delete_backup`, `restore_backup_to_new_cluster`, `restore_backup` (in place,
overwrites), `create_backup_schedule`, `delete_backup_schedule`, `create_database_api_key` (remembered so data tools work at once),
`delete_database_api_key`, `list_remembered_keys`, `forget_database_api_key`

**Data, read-only:** `list_collections`, `get_collection`, `list_payload_indexes`, `count_points`, `get_points`,
`scroll_points`, `query_points` (dense or sparse), `hybrid_query_points` (dense + sparse, RRF or DBSF fusion)

**Data, changes things:** `create_collection` (single, named and sparse vectors), `delete_collection`,
`create_payload_index`, `delete_payload_index`, `upsert_points`, `delete_points`

**Text:** `store_text` (embeds and upserts, creates the collection if missing), `find_text` (embeds the query and
searches)

## Compared with the official mcp-server-qdrant

| | official `mcp-server-qdrant` | `qdrant-full-mcp` |
|---|---|---|
| Store and find text with built-in embeddings | yes | yes (`store_text`, `find_text`) |
| Default collection via `COLLECTION_NAME`, one-step store | yes | yes |
| Local mode without a server (`QDRANT_LOCAL_PATH`) | yes (store/find) | yes (all data and text tools) |
| Custom tool descriptions, default search limit | yes | yes (same variable names) |
| Docker image | yes | yes |
| Cluster management (create, scale, suspend, delete) | no | yes |
| Backups, schedules, restores, database API keys | no | yes |
| Collections with named and sparse vectors, payload indexes | no | yes |
| Dense, sparse and hybrid (RRF, DBSF) search with your own vectors | no | yes |
| Dry run and confirmation on writes, read-only mode | read-only flag | yes |
| Transports | stdio, SSE, streamable HTTP | stdio, SSE, streamable HTTP |

The text tools use the same collection layout, so the two servers can share a collection.

## Safety

- Every mutating tool is a **dry run unless `confirm=true`**, and the tool descriptions tell the model to get your approval first.
- Irreversible tools need the exact name: `delete_cluster` and `restore_backup` (`confirm_cluster_name`), `delete_collection` (`confirm_collection_name`).
- `QDRANT_MCP_READ_ONLY=1` (or `QDRANT_READ_ONLY=1`) blocks every confirmed write. `QDRANT_MCP_MODULES` (e.g. `cloud`, `data,text`) loads only the modules you list.
- Keys come from environment variables only and are never logged or echoed. A new database key's secret is shown once by Qdrant.
- Ids and paths are validated before any request. Prefer least-privilege keys.

## Configuration

| Variable | Needed for | Notes |
|---|---|---|
| `QDRANT_CLOUD_API_KEY` | cloud tools | the only required variable |
| `QDRANT_CLOUD_ACCOUNT_ID` | optional | auto-detected when the key has one account; set it if you have several |
| `QDRANT_API_KEY` | data tools | database API key (optional for unauthenticated local Qdrant) |
| `QDRANT_API_KEY_<CLUSTER_ID>` | data tools | per-cluster key; wins over remembered keys and `QDRANT_API_KEY` |
| `QDRANT_MCP_KEY_FILE` | optional | where saved database keys go; `off` disables saving and reading |
| `QDRANT_URL` | data tools | only without `cluster_id`, e.g. `http://localhost:6333` |
| `QDRANT_LOCAL_PATH` | data tools | run without a server: a folder, or `:memory:`; instead of `QDRANT_URL` |
| `QDRANT_MCP_MODULES` | optional | `cloud,data,text` (default) |
| `COLLECTION_NAME` | text tools | default collection; stores into it skip the dry run |
| `EMBEDDING_MODEL` | text tools | FastEmbed model, default `sentence-transformers/all-MiniLM-L6-v2` |
| `QDRANT_MCP_READ_ONLY` | optional | `1` blocks writes (`QDRANT_READ_ONLY` also works) |
| `QDRANT_SEARCH_LIMIT` | text tools | default number of `find_text` results, 1..100 (default 5) |
| `TOOL_STORE_DESCRIPTION`, `TOOL_FIND_DESCRIPTION` | text tools | replace the tool descriptions |
| `QDRANT_MCP_TRANSPORT` | optional | `stdio` (default), `sse` or `streamable-http`; same as `--transport` |
| `FASTMCP_SERVER_HOST`, `FASTMCP_SERVER_PORT` | optional | HTTP bind address and port (`FASTMCP_HOST`/`FASTMCP_PORT` also work) |
| `QDRANT_CLOUD_API_URL` | optional | default `https://api.cloud.qdrant.io` |

See `.env.example`. Never commit real values.

## Status and roadmap

Verified against the live Qdrant Cloud API on a free-tier cluster: cluster create and update, restart, suspend and
unsuspend, database API keys, and the data tools (collections, named and sparse vectors, upsert, dense, sparse and
hybrid search, payload indexes, deletes). The text tools were verified end to end through the MCP protocol (stdio and
streamable HTTP) against a local Qdrant 1.19 with the real FastEmbed model, including a first run with an empty model
cache. Local mode (`QDRANT_LOCAL_PATH`) was verified for every data tool, including dense, sparse and hybrid search, and
through the MCP protocol; the Docker image was verified over HTTP from a second container. The text tools and data tools were also verified on a
Qdrant Cloud cluster: 26 checks covering store/find, metadata filters, default collection, read-only mode, named and
sparse collections, payload indexes, dense, sparse and hybrid search, deletes, and a stdio server process calling
`find_text` on Cloud. The remembered-key flow was verified on Cloud through the MCP protocol: create a key with
`remember="save"`, store and search in the same session, then search again from a fresh server process with no key in
its config, then delete the key and confirm the saved copy is gone.

Not verified live yet: backups, backup schedules and restores (free-tier clusters do not support them), and cluster
delete. Test those on a throwaway paid cluster first.

Ideas for later: snapshots, named-vector search options (quantization, HNSW tuning), payload updates without
re-upserting, collection aliases, cluster configuration settings.

**Not covered by design:** inviting or removing members, roles and billing; hybrid-cloud environments and serverless
spaces; embedding models other than FastEmbed's (for the other tools, bring your own vectors).

## Development

```bash
pip install -e ".[dev]"
pytest
```

Cloud API schemas: https://github.com/qdrant/qdrant-cloud-public-api. The Cloud REST gateway expects snake_case query
parameters (e.g. `page_size`) and accepts camelCase JSON bodies.

## License

MIT. Unofficial community project, not affiliated with or endorsed by Qdrant. "Qdrant" is a trademark of its owner.
