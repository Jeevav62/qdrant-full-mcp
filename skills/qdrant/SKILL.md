---
name: qdrant
description: Use when the user wants to manage Qdrant Cloud (clusters, regions, packages, backups, database API keys) or work with Qdrant collections and points (create collections, upsert, search, hybrid search, payload indexes, store and find plain text) through the qdrant MCP tools.
---

# Working with Qdrant through the qdrant MCP tools

## Two layers, two credentials
- **Cloud management** (`list_clusters`, `create_cluster`, `create_backup`, ...) uses the Cloud Management Key
  (`QDRANT_CLOUD_API_KEY`). It manages infrastructure only; it cannot read or write vectors.
- **Data** (`list_collections`, `upsert_points`, `query_points`, ...) talks to one cluster with a database API key
  (`QDRANT_API_KEY` or `QDRANT_API_KEY_<CLUSTER_ID>`). Pass `cluster_id` to target a Cloud cluster; the endpoint is
  looked up automatically. Without `cluster_id` the tools use `QDRANT_URL` (also fine for local Qdrant).

## Creating a cluster
1. `list_cloud_providers` -> `list_regions` (resolve "London" to a region id such as `eu-west-2`).
2. `list_packages` for that provider and region; show the user size and hourly price.
3. `create_cluster` with `confirm=false` first, summarize, and only call with `confirm=true` after the user agrees.
4. Poll `get_cluster` until `state.phase` is healthy, then `create_database_api_key` for data access.

## Rules
- Every change is a dry run until `confirm=true`. Never set `confirm=true` unprompted; show what will happen and ask.
- Deleting a cluster or collection, or restoring in place, needs the exact name from the user. Do not guess it.
- Prefer `restore_backup_to_new_cluster` over `restore_backup` (which overwrites the original).
- A new database API key's secret is shown once. Tell the user to store it; do not repeat it.
- Prefer least privilege: `read_only` keys, or per-collection rules.
- For plain text ("remember this", "what do we know about X") use `store_text` and `find_text`; they embed text
  locally, no vectors needed. `upsert_points`/`query_points` are for ready-made vectors.
- Hybrid search needs a collection with a named dense vector and a named sparse vector
  (`create_collection` with `named_vectors` and `sparse_vectors`).
