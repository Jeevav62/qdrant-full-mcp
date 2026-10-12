"""Qdrant Cloud account-level management tools (official Cloud API)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import keystore
from .common import (QdrantError, acct, cidrs, cloud_call, page, require_writable, segment, tool, uuid)

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,64}$")
VERSION_RE = re.compile(r"^(v\d+\.\d+\.\d+|latest)$")
KEY_NAME_RE = re.compile(r"^[\w\s-]{4,128}$")
DISPLAY_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")
CRON_RE = re.compile(r"^([\d*/,-]+ ){4}[\d*/,-]+$")  # 5-field cron; the API rejects '@daily' style (verified live)

CLUSTERS = lambda: f"/api/cluster/v1/accounts/{acct()}/clusters"  # noqa: E731
BACKUPS = lambda: f"/api/cluster/backup/v1/accounts/{acct()}"  # noqa: E731


def _dry(message: str, **data: Any) -> dict:
    return {"dry_run": True, "message": message, **data}


def _cluster(cluster_id: str) -> dict:
    r = cloud_call("GET", f"{CLUSTERS()}/{uuid(cluster_id, 'cluster_id')}")
    return r.get("cluster", r)


# ---------------------------------------------------------------- read-only


@tool
def list_accounts() -> Any:
    """List Qdrant Cloud accounts visible to the management key (id, name, owner). The server uses the only
    account automatically; if there are several, set QDRANT_CLOUD_ACCOUNT_ID to pick one."""
    return cloud_call("GET", "/api/account/v1/accounts")


@tool
def list_clusters(cloud_provider_id: Optional[str] = None, cloud_provider_region_id: Optional[str] = None,
                  page_size: int = 100, page_token: Optional[str] = None) -> Any:
    """List Qdrant Cloud clusters in the configured account. Use first when the user asks what clusters exist.
    Optional filters: cloud_provider_id (e.g. 'aws'), cloud_provider_region_id (requires provider).
    Paginated: pass next_page_token from the result as page_token for more."""
    if cloud_provider_region_id and not cloud_provider_id:
        raise QdrantError("cloud_provider_id is required when cloud_provider_region_id is set.")
    return cloud_call("GET", CLUSTERS(), params={
        "cloud_provider_id": cloud_provider_id, "cloud_provider_region_id": cloud_provider_region_id,
        "page_size": page(page_size), "page_token": page_token})


@tool
def get_cluster(cluster_id: str) -> Any:
    """Get full details of one cluster by UUID: config, state/phase, database endpoint URL, nodes.
    Use list_clusters to find IDs. The endpoint URL is what you put in QDRANT_URL for the data tools."""
    return cloud_call("GET", f"{CLUSTERS()}/{uuid(cluster_id, 'cluster_id')}")


@tool
def list_cloud_providers() -> Any:
    """List cloud providers (aws, gcp, azure, hybrid) available to the account, with availability flags."""
    return cloud_call("GET", f"/api/platform/v1/accounts/{acct()}/cloud-providers")


@tool
def list_regions(cloud_provider_id: str) -> Any:
    """List regions for one cloud provider (e.g. 'aws'): region ids (e.g. 'eu-west-2' = London), names,
    availability, free-tier flag. Use to resolve a place name to a region id before create_cluster."""
    p = segment(cloud_provider_id, "cloud_provider_id")
    return cloud_call("GET", f"/api/platform/v1/accounts/{acct()}/cloud-providers/{p}/regions")


@tool
def list_packages(cloud_provider_id: str, cloud_provider_region_id: Optional[str] = None,
                  multi_az: Optional[bool] = None, gpu: Optional[bool] = None) -> Any:
    """List bookable cluster packages (CPU/RAM/disk + hourly price) for a provider/region.
    Needed for the package_id in create_cluster/update_cluster. Region required unless provider is 'hybrid'."""
    p = segment(cloud_provider_id, "cloud_provider_id")
    if p != "hybrid" and not cloud_provider_region_id:
        raise QdrantError("cloud_provider_region_id is required unless provider is 'hybrid'.")
    return cloud_call("GET", f"/api/booking/v1/accounts/{acct()}/packages", params={
        "cloud_provider_id": p, "cloud_provider_region_id": cloud_provider_region_id,
        "multi_az": None if multi_az is None else str(multi_az).lower(),
        "gpu": None if gpu is None else str(gpu).lower()})


@tool
def list_releases() -> Any:
    """List Qdrant versions that clusters in this account may run (valid values for create/update 'version')."""
    return cloud_call("GET", f"/api/cluster/v1/accounts/{acct()}/releases")


@tool
def list_backups(cluster_id: Optional[str] = None, page_size: int = 100, page_token: Optional[str] = None) -> Any:
    """List backups in the account, optionally only for one cluster (cluster_id UUID)."""
    if cluster_id:
        uuid(cluster_id, "cluster_id")
    return cloud_call("GET", f"{BACKUPS()}/backups", params={
        "cluster_id": cluster_id, "page_size": page(page_size), "page_token": page_token})


@tool
def list_backup_schedules(cluster_id: Optional[str] = None) -> Any:
    """List automatic backup schedules (cron + retention), optionally filtered client-side to one cluster."""
    r = cloud_call("GET", f"{BACKUPS()}/backup_schedules")
    if cluster_id and isinstance(r, dict) and "items" in r:
        uuid(cluster_id, "cluster_id")
        r["items"] = [i for i in r["items"] if i.get("clusterId") == cluster_id]
    return r


@tool
def list_database_api_keys(cluster_id: Optional[str] = None) -> Any:
    """List database API keys (metadata only; secrets are never retrievable after creation)."""
    if cluster_id:
        uuid(cluster_id, "cluster_id")
    return cloud_call("GET", f"/api/cluster/auth/v2/accounts/{acct()}/database-api-keys",
                      params={"cluster_id": cluster_id})


@tool
def list_members() -> Any:
    """List members of the Qdrant Cloud account (read-only)."""
    return cloud_call("GET", f"/api/account/v1/accounts/{acct()}/members")


# ------------------------------------------------------- cluster management


@tool
def create_cluster(name: str, cloud_provider_id: str, cloud_provider_region_id: str, package_id: str,
                   number_of_nodes: int = 1, version: str = "latest", additional_disk_gib: int = 0,
                   allowed_ip_source_ranges: Optional[list[str]] = None, confirm: bool = False) -> Any:
    """Create a NEW Qdrant Cloud cluster. COSTS MONEY (billed hourly for paid packages).
    Workflow: list_cloud_providers -> list_regions -> list_packages to get ids, summarize spec and price to the
    user, and call with confirm=true only after they explicitly approve. confirm=false = dry run, nothing created.
    name: 2-64 chars [a-zA-Z0-9_-]. version: 'latest' or 'vX.Y.Z'. allowed_ip_source_ranges: IPv4 CIDRs."""
    if not NAME_RE.match(name or ""):
        raise QdrantError("name must be 2-64 chars of letters, numbers, '_' or '-'.")
    segment(cloud_provider_id, "cloud_provider_id")
    segment(cloud_provider_region_id, "cloud_provider_region_id")
    uuid(package_id, "package_id")
    if not 1 <= number_of_nodes <= 100:
        raise QdrantError("number_of_nodes must be 1..100.")
    if not VERSION_RE.match(version):
        raise QdrantError("version must be 'latest' or like 'v1.15.0'.")
    if additional_disk_gib < 0:
        raise QdrantError("additional_disk_gib must be >= 0.")
    cfg: dict[str, Any] = {"numberOfNodes": number_of_nodes, "version": version, "packageId": package_id}
    if additional_disk_gib:
        cfg["additionalResources"] = {"disk": additional_disk_gib}
    if allowed_ip_source_ranges:
        cfg["allowedIpSourceRanges"] = cidrs(allowed_ip_source_ranges)
    cluster = {"accountId": acct(), "name": name, "cloudProviderId": cloud_provider_id,
               "cloudProviderRegionId": cloud_provider_region_id, "configuration": cfg}
    if not confirm:
        return _dry("Nothing created. Ask the user to approve, then call again with confirm=true.",
                    would_create=cluster)
    require_writable()
    return cloud_call("POST", CLUSTERS(), body={"cluster": cluster})


@tool
def update_cluster(cluster_id: str, name: Optional[str] = None, number_of_nodes: Optional[int] = None,
                   package_id: Optional[str] = None, version: Optional[str] = None,
                   additional_disk_gib: Optional[int] = None,
                   allowed_ip_source_ranges: Optional[list[str]] = None, confirm: bool = False) -> Any:
    """Modify an existing cluster (rename, scale nodes, change package/version, extra disk, IP allow-list).
    Only passed fields change (update mask). Scaling/package/version changes can restart nodes and change billing;
    downgrades may be refused; provider/region are immutable. confirm=false = dry run; confirm=true only after
    user approval."""
    uuid(cluster_id, "cluster_id")
    mask: list[str] = []
    cluster: dict[str, Any] = {"id": cluster_id, "accountId": acct()}
    cfg: dict[str, Any] = {}
    if name is not None:
        if not NAME_RE.match(name):
            raise QdrantError("name must be 2-64 chars of letters, numbers, '_' or '-'.")
        cluster["name"] = name
        mask.append("name")
    if number_of_nodes is not None:
        if not 1 <= number_of_nodes <= 100:
            raise QdrantError("number_of_nodes must be 1..100.")
        cfg["numberOfNodes"] = number_of_nodes
        mask.append("configuration.number_of_nodes")
    if package_id is not None:
        cfg["packageId"] = uuid(package_id, "package_id")
        mask.append("configuration.package_id")
    if version is not None:
        if not VERSION_RE.match(version):
            raise QdrantError("version must be 'latest' or like 'v1.15.0'.")
        cfg["version"] = version
        mask.append("configuration.version")
    if additional_disk_gib is not None:
        if additional_disk_gib < 0:
            raise QdrantError("additional_disk_gib must be >= 0.")
        cfg["additionalResources"] = {"disk": additional_disk_gib}
        mask.append("configuration.additional_resources")
    if allowed_ip_source_ranges is not None:
        cfg["allowedIpSourceRanges"] = cidrs(allowed_ip_source_ranges)
        mask.append("configuration.allowed_ip_source_ranges")
    if not mask:
        raise QdrantError("Nothing to update: pass at least one field to change.")
    if cfg:
        cluster["configuration"] = cfg
    current = _cluster(cluster_id)
    if not confirm:
        return _dry("Nothing changed. Get user approval, then call again with confirm=true.",
                    cluster_name=current.get("name"), fields_to_change=mask, new_values=cluster)
    require_writable()
    # Gateway wants updateMask as {"paths": [...]} with snake_case field paths (verified live).
    return cloud_call("PUT", f"{CLUSTERS()}/{cluster_id}", body={"cluster": cluster, "updateMask": {"paths": mask}})


@tool
def delete_cluster(cluster_id: str, confirm_cluster_name: str, delete_backups: bool = False) -> Any:
    """PERMANENTLY DELETE a cluster and its data. IRREVERSIBLE. Never call unless the user explicitly asked to
    delete this specific cluster. Show the user the cluster name/id first, have them confirm the exact name, then
    pass it as confirm_cluster_name; it must match the real name or nothing is deleted.
    delete_backups=true also removes the cluster's backups (default false: backups kept)."""
    uuid(cluster_id, "cluster_id")
    require_writable()
    real = _cluster(cluster_id).get("name")
    if not real or confirm_cluster_name != real:
        raise QdrantError(f"Confirmation failed: confirm_cluster_name {confirm_cluster_name!r} does not match the "
                          f"cluster's actual name {real!r}. Nothing deleted.")
    r = cloud_call("DELETE", f"{CLUSTERS()}/{cluster_id}",
                   params={"delete_backups": "true" if delete_backups else None})
    keystore.forget(cluster_id)  # its keys die with it
    return r


def _lifecycle(action: str, cluster_id: str, confirm: bool, effect: str) -> Any:
    uuid(cluster_id, "cluster_id")
    current = _cluster(cluster_id)
    if not confirm:
        return _dry(f"Nothing done. {effect} Get user approval, then call again with confirm=true.",
                    action=action, cluster_name=current.get("name"),
                    phase=(current.get("state") or {}).get("phase"))
    require_writable()
    return cloud_call("POST", f"{CLUSTERS()}/{cluster_id}/{action}")


@tool
def restart_cluster(cluster_id: str, confirm: bool = False) -> Any:
    """Restart a cluster's nodes (causes brief unavailability). confirm=false = dry run; true only after approval."""
    return _lifecycle("restart", cluster_id, confirm, "Restart interrupts the database briefly.")


@tool
def suspend_cluster(cluster_id: str, confirm: bool = False) -> Any:
    """Suspend a cluster (database goes offline until unsuspended; data kept). confirm=false = dry run."""
    return _lifecycle("suspend", cluster_id, confirm, "Suspend takes the database offline.")


@tool
def unsuspend_cluster(cluster_id: str, confirm: bool = False) -> Any:
    """Resume a suspended cluster (billing resumes). confirm=false = dry run."""
    return _lifecycle("unsuspend", cluster_id, confirm, "Unsuspend resumes the cluster and its billing.")


# ----------------------------------------------------------------- backups


@tool
def create_backup(cluster_id: str, display_name: Optional[str] = None, retention_days: Optional[int] = None,
                  confirm: bool = False) -> Any:
    """Create an on-demand backup of a cluster. Backup storage may be billed. display_name: 4-64 chars
    [a-zA-Z0-9_-]. retention_days 1..365 (omit = keep until deleted). confirm=false = dry run."""
    uuid(cluster_id, "cluster_id")
    b: dict[str, Any] = {"accountId": acct(), "clusterId": cluster_id}
    if display_name:
        if not DISPLAY_NAME_RE.match(display_name):
            raise QdrantError("display_name must be 4-64 chars of letters, digits, '-' or '_'.")
        b["displayName"] = display_name
    if retention_days is not None:
        if not 1 <= retention_days <= 365:
            raise QdrantError("retention_days must be 1..365.")
        b["retentionPeriod"] = f"{retention_days * 86400}s"
    if not confirm:
        return _dry("Nothing created. Get user approval, then call again with confirm=true.", would_create=b)
    require_writable()
    return cloud_call("POST", f"{BACKUPS()}/backups", body={"backup": b})


@tool
def delete_backup(backup_id: str, confirm: bool = False) -> Any:
    """PERMANENTLY delete one backup. IRREVERSIBLE. confirm=false = dry run showing the backup; true only after
    the user explicitly approves deleting that backup."""
    uuid(backup_id, "backup_id")
    r = cloud_call("GET", f"{BACKUPS()}/backups/{backup_id}")
    b = r.get("backup", r)
    if not confirm:
        return _dry("Nothing deleted. Get explicit user approval, then call again with confirm=true.", backup=b)
    require_writable()
    return cloud_call("DELETE", f"{BACKUPS()}/backups/{backup_id}")


@tool
def restore_backup(backup_id: str, confirm_cluster_name: str) -> Any:
    """Restore a backup INTO ITS ORIGINAL CLUSTER, overwriting that cluster's current data. DESTRUCTIVE.
    Prefer restore_backup_to_new_cluster unless the user explicitly wants an in-place overwrite.
    The user must confirm the target cluster's exact name, passed as confirm_cluster_name; it must match or nothing
    happens. Use list_backups first to pick the backup."""
    uuid(backup_id, "backup_id")
    require_writable()
    r = cloud_call("GET", f"{BACKUPS()}/backups/{backup_id}")
    cluster_id = r.get("backup", r).get("clusterId")
    real = _cluster(cluster_id).get("name") if cluster_id else None
    if not real or confirm_cluster_name != real:
        raise QdrantError(f"Confirmation failed: confirm_cluster_name {confirm_cluster_name!r} does not match the "
                          f"target cluster's name {real!r}. Nothing restored.")
    return cloud_call("POST", f"{BACKUPS()}/backups/{backup_id}/restore")


@tool
def restore_backup_to_new_cluster(backup_id: str, new_cluster_name: str, confirm: bool = False) -> Any:
    """Create a NEW cluster from a backup. Safer than restore_backup because the source cluster is untouched.
    COSTS MONEY (a new cluster is billed). new_cluster_name: 4-64 chars [a-zA-Z0-9_-]. confirm=false = dry run
    showing the backup; call with confirm=true only after the user approves."""
    uuid(backup_id, "backup_id")
    if not re.match(r"^[a-zA-Z0-9_-]{4,64}$", new_cluster_name or ""):
        raise QdrantError("new_cluster_name must be 4-64 chars of letters, numbers, '_' or '-'.")
    if not confirm:
        r = cloud_call("GET", f"{BACKUPS()}/backups/{backup_id}")
        return _dry("Nothing created. Get user approval, then call again with confirm=true.",
                    new_cluster_name=new_cluster_name, backup=r.get("backup", r))
    require_writable()
    return cloud_call("POST", f"/api/cluster/v1/accounts/{acct()}/backups/{backup_id}/clusters",
                      params={"cluster_name": new_cluster_name})


@tool
def create_backup_schedule(cluster_id: str, schedule: str, retention_days: int = 7,
                           confirm: bool = False) -> Any:
    """Create an automatic backup schedule (not available on free-tier clusters).
    schedule: 5-field cron, e.g. '0 3 * * *' = daily at 03:00 UTC. retention_days 1..365 (required by the API,
    default 7). confirm=false = dry run."""
    uuid(cluster_id, "cluster_id")
    if not CRON_RE.match(schedule or ""):
        raise QdrantError("schedule must be a 5-field cron expression like '0 3 * * *'.")
    if not 1 <= retention_days <= 365:
        raise QdrantError("retention_days must be 1..365.")
    s: dict[str, Any] = {"accountId": acct(), "clusterId": cluster_id, "schedule": schedule,
                         "retentionPeriod": f"{retention_days * 86400}s"}
    if not confirm:
        return _dry("Nothing created. Get user approval, then call again with confirm=true.", would_create=s)
    require_writable()
    return cloud_call("POST", f"{BACKUPS()}/backup_schedules", body={"backupSchedule": s})


@tool
def delete_backup_schedule(backup_schedule_id: str, confirm: bool = False) -> Any:
    """Delete an automatic backup schedule (existing backups are kept). confirm=false = dry run."""
    uuid(backup_schedule_id, "backup_schedule_id")
    if not confirm:
        return _dry("Nothing deleted. Get user approval, then call again with confirm=true.",
                    backup_schedule_id=backup_schedule_id)
    require_writable()
    return cloud_call("DELETE", f"{BACKUPS()}/backup_schedules/{backup_schedule_id}")


# ---------------------------------------------------- database API keys


@tool
def create_database_api_key(cluster_id: str, name: str, access: str = "read_only",
                            collection_access: Optional[list[dict]] = None, expires_in_days: Optional[int] = None,
                            remember: str = "session", confirm: bool = False) -> Any:
    """Create a database API key for a cluster, so the data and text tools can read and write it. The secret is
    returned ONCE in the response: never repeat it unnecessarily.
    Scope: either access='read_only'|'manage' (global; default read_only = least privilege; use 'manage' when the user
    wants to create collections or write data) OR collection_access=[{"collection_name": "docs",
    "access": "read_only"|"read_write"}] (per collection, max 20).
    remember: 'session' (default) = this server uses the key for this cluster right away, no restart or config edit;
    'save' = also store it in a local key file so future sessions in any MCP client work too. ASK THE USER before
    using 'save' ("Save this key on this machine so it works next time?"); 'none' = only return it.
    name: 4-128 chars letters/digits/space/_/-. expires_in_days optional. confirm=false = dry run."""
    uuid(cluster_id, "cluster_id")
    if remember not in ("none", "session", "save"):
        raise QdrantError("remember must be 'none', 'session' or 'save'.")
    if not KEY_NAME_RE.match(name or ""):
        raise QdrantError("name must be 4-128 chars of letters, digits, spaces, '_' or '-'.")
    if collection_access:
        if len(collection_access) > 20:
            raise QdrantError("collection_access allows at most 20 rules.")
        rules = []
        for rule in collection_access:
            acc = str(rule.get("access", "")).lower()
            if acc not in ("read_only", "read_write"):
                raise QdrantError("collection_access[].access must be 'read_only' or 'read_write'.")
            rules.append({"collectionAccess": {
                "collectionName": segment(rule.get("collection_name"), "collection_name"),
                "accessType": f"COLLECTION_ACCESS_RULE_ACCESS_TYPE_{acc.upper()}"}})
    else:
        if access not in ("read_only", "manage"):
            raise QdrantError("access must be 'read_only' or 'manage'.")
        rules = [{"globalAccess": {"accessType": f"GLOBAL_ACCESS_RULE_ACCESS_TYPE_{access.upper()}"}}]
    key: dict[str, Any] = {"accountId": acct(), "clusterId": cluster_id, "name": name, "accessRules": rules}
    if expires_in_days is not None:
        if not 1 <= expires_in_days <= 3650:
            raise QdrantError("expires_in_days must be 1..3650.")
        key["expiresAt"] = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not confirm:
        return _dry("Nothing created. Get user approval, then call again with confirm=true.", would_create=key,
                    remember=remember)
    require_writable()
    r = cloud_call("POST", f"/api/cluster/auth/v2/accounts/{acct()}/database-api-keys",
                   body={"databaseApiKey": key})
    created = r.get("databaseApiKey", r) if isinstance(r, dict) else {}
    secret = created.get("key")
    if remember != "none" and secret:
        try:
            path = keystore.remember(cluster_id, secret, created.get("id"), name, save=remember == "save")
            r["remembered"] = {"mode": remember, "key_file": path,
                               "note": "Data tools on this cluster now use this key automatically."}
        except QdrantError as e:  # key exists in Qdrant either way; report instead of failing
            r["remembered"] = {"mode": "session" if keystore.get(cluster_id) == secret else "none", "error": str(e)}
    return r


@tool
def delete_database_api_key(cluster_id: str, database_api_key_id: str, confirm: bool = False) -> Any:
    """Revoke (delete) a database API key; apps using it lose access immediately. confirm=false = dry run."""
    uuid(cluster_id, "cluster_id")
    uuid(database_api_key_id, "database_api_key_id")
    if not confirm:
        return _dry("Nothing deleted. Get user approval, then call again with confirm=true.",
                    cluster_id=cluster_id, database_api_key_id=database_api_key_id)
    require_writable()
    r = cloud_call("DELETE", f"/api/cluster/auth/v2/accounts/{acct()}/database-api-keys/{database_api_key_id}",
                   params={"cluster_id": cluster_id})
    forgotten = keystore.forget(cluster_id, key_id=database_api_key_id)
    if any(forgotten.values()):
        r = {**(r if isinstance(r, dict) else {}), "forgotten_locally": forgotten}
    return r


@tool
def list_remembered_keys() -> Any:
    """Database keys this server remembers (from create_database_api_key), per cluster: key id, name, and whether it
    is kept for this session or saved in the local key file. Secrets are never shown."""
    return {"key_file": str(keystore.key_file() or "disabled"), "keys": keystore.summary()}


@tool
def forget_database_api_key(cluster_id: str) -> Any:
    """Stop using the remembered database key for a cluster (removes it from this session and the local key file).
    It does NOT revoke the key in Qdrant; use delete_database_api_key for that."""
    return {"cluster_id": uuid(cluster_id, "cluster_id"), "forgotten": keystore.forget(cluster_id)}
