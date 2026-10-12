---
name: qdrant
description: Use when the user wants to manage Qdrant Cloud (clusters, regions, packages, backups, database API keys) or work with Qdrant collections and points (create collections, upsert, search, hybrid search, payload indexes, store and find plain text) through the qdrant MCP tools.
---

# Working with Qdrant through the qdrant MCP tools

## Two layers, two credentials
- **Cloud management** (`list_clusters`, `create_cluster`, `create_backup`, ...) uses the Cloud Management Key
  (`QDRANT_CLOUD_API_KEY`). It manages infrastructure only; it cannot read or write vectors.
- **Data** (`list_collections`, `upsert_points`, `query_points`, ...) talks to one cluster with a database API key.
  Pass `cluster_id` to target a Cloud cluster; the endpoint is looked up automatically. Without `cluster_id` the
  tools use `QDRANT_URL` or `QDRANT_LOCAL_PATH`.
- If a data tool says there is no database key, create one with `create_database_api_key`: the server remembers it
  and data tools work immediately, no restart or config edit. Ask the user once whether to save it on this machine
  for future sessions; if yes pass `remember="save"`, otherwise leave the default `remember="session"`.

## Creating a cluster
1. `list_cloud_providers` -> `list_regions` (resolve "London" to a region id such as `eu-west-2`).
2. `list_packages` for that provider and region; show the user size and hourly price.
3. `create_cluster` with `confirm=false` first, summarize, and only call with `confirm=true` after the user agrees.
4. Poll `get_cluster` until `state.phase` is healthy, then `create_database_api_key` (access `manage` if the user
   will create collections or write data) so the data tools work right away.

## Rules
- Every change is a dry run until `confirm=true`. Never set `confirm=true` unprompted; show what will happen and ask.
- Deleting a cluster or collection, or restoring in place, needs the exact name from the user. Do not guess it.
- Prefer `restore_backup_to_new_cluster` over `restore_backup` (which overwrites the original).
- A new database API key's secret is shown once. Do not repeat it. Mention that it is remembered for this session
  (or saved, if the user agreed) and that they need it themselves only for their own apps.
- On Qdrant Cloud, filtering on a field needs a payload index first (`create_payload_index`, usually `keyword`).
- Prefer least privilege: `read_only` keys, or per-collection rules.
- For plain text ("remember this", "what do we know about X") use `store_text` and `find_text`; they embed text
  locally, no vectors needed. `upsert_points`/`query_points` are for ready-made vectors.
- Hybrid search needs a collection with a named dense vector and a named sparse vector
  (`create_collection` with `named_vectors` and `sparse_vectors`).
