import httpx
import pytest

from qdrant_full_mcp import cloud, common, data

ACCT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CID = "11111111-1111-1111-1111-111111111111"
PKG = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("QDRANT_CLOUD_API_KEY", "k")
    monkeypatch.setenv("QDRANT_CLOUD_ACCOUNT_ID", ACCT)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "dbkey")
    monkeypatch.delenv("QDRANT_MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("QDRANT_CLOUD_READ_ONLY", raising=False)


@pytest.fixture
def calls(monkeypatch):
    class Log(list):
        routes: dict

    log = Log()
    routes = {}

    def fake(method, url, **kw):
        log.append((method, url, kw))
        for (m, suffix), resp in routes.items():
            if m == method and url.split("?")[0].endswith(suffix):
                return httpx.Response(200, json=resp, request=httpx.Request(method, url))
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr(common.httpx, "request", fake)
    log.routes = routes
    return log


def writes(log):
    return [c for c in log if c[0] != "GET" and not c[1].endswith("/points/count")]


def test_auth_header_and_snake_case_params(calls):
    cloud.list_clusters(page_size=5)
    method, url, kw = calls[0]
    assert kw["headers"]["Authorization"] == "apikey k"
    assert kw["params"] == {"page_size": 5}
    assert f"/accounts/{ACCT}/clusters" in url


def test_input_validation():
    for fn, args in [
        (cloud.get_cluster, ("bad",)),
        (cloud.list_regions, ("../x",)),
        (data.get_collection, ("a/b",)),
    ]:
        with pytest.raises(ValueError):
            fn(*args)
    with pytest.raises(ValueError):
        cloud.create_cluster("x", "aws", "eu-west-2", PKG)  # name too short
    with pytest.raises(ValueError):
        cloud.create_database_api_key(CID, "abcd", access="root")


def test_dry_runs_never_write(calls):
    cloud.create_cluster("demo-1", "aws", "eu-west-2", PKG)
    cloud.update_cluster(CID, number_of_nodes=3)
    cloud.restart_cluster(CID)
    cloud.suspend_cluster(CID)
    cloud.create_backup(CID)
    cloud.delete_backup(CID)
    cloud.create_backup_schedule(CID, "0 3 * * *")
    cloud.create_database_api_key(CID, "my key")
    cloud.delete_database_api_key(CID, PKG)
    data.create_collection("c", 4)
    data.upsert_points("c", [{"id": 1, "vector": [0.1, 0.2]}])
    data.delete_points("c", ids=[1])
    assert writes(calls) == []


def test_delete_cluster_requires_matching_name(calls):
    calls.routes[("GET", f"/clusters/{CID}")] = {"cluster": {"name": "prod"}}
    with pytest.raises(ValueError, match="Confirmation failed"):
        cloud.delete_cluster(CID, "wrong")
    assert writes(calls) == []
    cloud.delete_cluster(CID, "prod")
    assert writes(calls)[0][0] == "DELETE"


def test_restore_requires_target_cluster_name(calls):
    bid = "33333333-3333-3333-3333-333333333333"
    calls.routes[("GET", f"/backups/{bid}")] = {"backup": {"clusterId": CID}}
    calls.routes[("GET", f"/clusters/{CID}")] = {"cluster": {"name": "prod"}}
    with pytest.raises(ValueError):
        cloud.restore_backup(bid, "nope")
    assert writes(calls) == []
    cloud.restore_backup(bid, "prod")
    assert writes(calls)[0][1].endswith(f"/backups/{bid}/restore")


def test_read_only_mode_blocks_confirmed_writes(calls, monkeypatch):
    monkeypatch.setenv("QDRANT_MCP_READ_ONLY", "1")
    with pytest.raises(ValueError, match="read-only"):
        cloud.restart_cluster(CID, confirm=True)
    with pytest.raises(ValueError, match="read-only"):
        data.upsert_points("c", [{"id": 1, "vector": [1.0]}], confirm=True)
    assert writes(calls) == []


def test_update_sends_mask(calls):
    calls.routes[("GET", f"/clusters/{CID}")] = {"cluster": {"name": "prod"}}
    cloud.update_cluster(CID, number_of_nodes=2, confirm=True)
    method, url, kw = writes(calls)[0]
    assert method == "PUT" and kw["json"]["updateMask"] == {"paths": ["configuration.number_of_nodes"]}


def test_data_plane_uses_api_key_header(calls):
    data.list_collections()
    assert calls[0][2]["headers"]["api-key"] == "dbkey"
    assert calls[0][1] == "http://qdrant.test:6333/collections"


def test_delete_collection_confirmation(calls):
    with pytest.raises(ValueError):
        data.delete_collection("a", "b")
    assert writes(calls) == []
    data.delete_collection("a", "a")
    assert writes(calls)[0][0] == "DELETE"


def test_missing_config_messages(monkeypatch):
    monkeypatch.delenv("QDRANT_URL")
    with pytest.raises(ValueError, match="QDRANT_URL"):
        data.list_collections()
    monkeypatch.delenv("QDRANT_CLOUD_API_KEY")
    with pytest.raises(ValueError, match="QDRANT_CLOUD_API_KEY"):
        cloud.list_clusters()


def test_api_error_is_mapped(monkeypatch):
    monkeypatch.setattr(common.httpx, "request", lambda m, u, **k: httpx.Response(
        403, json={"message": "nope"}, request=httpx.Request(m, u)))
    with pytest.raises(ValueError, match="403.*forbidden"):
        cloud.list_clusters()


def test_cluster_id_resolves_endpoint_and_key(calls, monkeypatch):
    common._endpoint_cache.clear()
    monkeypatch.setenv("QDRANT_API_KEY_" + CID.replace("-", "_").upper(), "percluster")
    calls.routes[("GET", f"/clusters/{CID}")] = {
        "cluster": {"state": {"endpoint": {"url": "https://abc.cloud.qdrant.io", "restPort": 6333}}}}
    data.list_collections(cluster_id=CID)
    method, url, kw = calls[-1]
    assert url == "https://abc.cloud.qdrant.io:6333/collections"
    assert kw["headers"]["api-key"] == "percluster"
    data.list_collections(cluster_id=CID)  # endpoint cached: only one cloud lookup
    assert sum(1 for c in calls if "/api/cluster/v1/" in c[1]) == 1


def test_cluster_id_requires_key(calls, monkeypatch):
    common._endpoint_cache.clear()
    monkeypatch.delenv("QDRANT_API_KEY")
    calls.routes[("GET", f"/clusters/{CID}")] = {"cluster": {"state": {"endpoint": {"url": "https://x.io"}}}}
    with pytest.raises(ValueError, match="No database API key"):
        data.list_collections(cluster_id=CID)


def test_cluster_without_endpoint(calls):
    common._endpoint_cache.clear()
    calls.routes[("GET", f"/clusters/{CID}")] = {"cluster": {"state": {}}}
    with pytest.raises(ValueError, match="no endpoint"):
        data.list_collections(cluster_id=CID)


def test_bad_cluster_id():
    with pytest.raises(ValueError):
        data.list_collections(cluster_id="nope")


def test_account_id_autodiscovered_once(calls, monkeypatch):
    common._account_cache.clear()
    monkeypatch.delenv("QDRANT_CLOUD_ACCOUNT_ID")
    calls.routes[("GET", "/api/account/v1/accounts")] = {"items": [{"id": ACCT, "name": "Base"}]}
    cloud.list_clusters()
    cloud.list_clusters()
    assert f"/accounts/{ACCT}/clusters" in calls[-1][1]
    assert sum(1 for c in calls if c[1].endswith("/api/account/v1/accounts")) == 1


def test_multiple_accounts_requires_explicit_id(calls, monkeypatch):
    common._account_cache.clear()
    monkeypatch.delenv("QDRANT_CLOUD_ACCOUNT_ID")
    calls.routes[("GET", "/api/account/v1/accounts")] = {"items": [{"id": ACCT, "name": "A"}, {"id": CID, "name": "B"}]}
    with pytest.raises(ValueError, match="QDRANT_CLOUD_ACCOUNT_ID"):
        cloud.list_clusters()


def test_invalid_account_id_env(monkeypatch):
    monkeypatch.setenv("QDRANT_CLOUD_ACCOUNT_ID", "nope")
    with pytest.raises(ValueError, match="valid UUID"):
        cloud.list_clusters()


# ---------------------------------------------------------------- v0.3


def test_restore_to_new_cluster_dry_run_then_post(calls):
    bid = "33333333-3333-3333-3333-333333333333"
    calls.routes[("GET", f"/backups/{bid}")] = {"backup": {"clusterId": CID}}
    out = cloud.restore_backup_to_new_cluster(bid, "restored-1")
    assert out["dry_run"] and writes(calls) == []
    with pytest.raises(ValueError):
        cloud.restore_backup_to_new_cluster(bid, "no")  # too short
    cloud.restore_backup_to_new_cluster(bid, "restored-1", confirm=True)
    method, url, kw = writes(calls)[0]
    assert method == "POST" and url.endswith(f"/backups/{bid}/clusters")
    assert kw["params"] == {"cluster_name": "restored-1"}


def test_create_collection_named_and_sparse(calls):
    out = data.create_collection("c", named_vectors={"dense": {"size": 4, "distance": "Dot"}},
                                 sparse_vectors=["sparse"])
    assert out["would_create"] == {"vectors": {"dense": {"size": 4, "distance": "Dot"}},
                                   "sparse_vectors": {"sparse": {}}}
    data.create_collection("c", named_vectors={"dense": {"size": 4}}, sparse_vectors=["sparse"], confirm=True)
    assert writes(calls)[0][2]["json"]["vectors"]["dense"]["distance"] == "Cosine"
    for bad in [dict(), dict(vector_size=4, named_vectors={"d": {"size": 4}}),
                dict(named_vectors={"d": {"size": 0}})]:
        with pytest.raises(ValueError):
            data.create_collection("c", **bad)


def test_upsert_named_and_sparse_vectors(calls):
    pt = {"id": 1, "vector": {"dense": [0.1, 0.2], "sparse": {"indices": [1, 3], "values": [0.5, 0.6]}}}
    data.upsert_points("c", [pt], confirm=True)
    assert writes(calls)[0][2]["json"]["points"][0]["vector"]["sparse"]["indices"] == [1, 3]
    for bad in ({"id": 1, "vector": {}}, {"id": 1, "vector": {"s": {"indices": [1], "values": []}}},
                {"id": 1, "vector": "x"}):
        with pytest.raises(ValueError):
            data.upsert_points("c", [bad])


def test_query_sparse_and_exclusive_args(calls):
    data.query_points("c", sparse_vector={"indices": [1], "values": [0.5]}, using="sparse")
    assert calls[-1][2]["json"]["query"] == {"indices": [1], "values": [0.5]}
    assert calls[-1][2]["json"]["using"] == "sparse"
    with pytest.raises(ValueError):
        data.query_points("c")
    with pytest.raises(ValueError):
        data.query_points("c", vector=[1.0], sparse_vector={"indices": [1], "values": [1.0]})
    with pytest.raises(ValueError, match="using"):
        data.query_points("c", sparse_vector={"indices": [1], "values": [0.5]})


def test_hybrid_query_body(calls):
    data.hybrid_query_points("c", [0.1, 0.2], {"indices": [1], "values": [0.5]}, fusion="dbsf", limit=5)
    body = calls[-1][2]["json"]
    assert body["query"] == {"fusion": "dbsf"} and len(body["prefetch"]) == 2
    assert {p["using"] for p in body["prefetch"]} == {"dense", "sparse"}
    data.hybrid_query_points("c", [0.1], {"indices": [1], "values": [0.5]})
    assert calls[-1][2]["json"]["query"] == {"rrf": {}}
    with pytest.raises(ValueError):
        data.hybrid_query_points("c", [0.1], {"indices": [1], "values": [0.5]}, fusion="mix")


def test_payload_indexes(calls):
    assert data.create_payload_index("c", "city", "keyword")["dry_run"]
    assert writes(calls) == []
    data.create_payload_index("c", "tenant", "keyword", is_tenant=True, confirm=True)
    method, url, kw = writes(calls)[0]
    assert method == "PUT" and url.endswith("/collections/c/index")
    assert kw["json"] == {"field_name": "tenant", "field_schema": {"type": "keyword", "is_tenant": True}}
    data.delete_payload_index("c", "meta.tags[].name", confirm=True)
    assert writes(calls)[-1][0] == "DELETE"
    for args in [("c", "city", "nonsense"), ("c", "../x", "keyword"), ("c", "n", "integer", True)]:
        with pytest.raises(ValueError):
            data.create_payload_index(*args)


def test_backup_schedule_cron_and_retention(calls):
    out = cloud.create_backup_schedule(CID, "0 3 * * *")
    assert out["would_create"]["retentionPeriod"] == "604800s"  # default 7 days, required by the API
    for bad in ("@daily", "every day", "0 3 * *"):
        with pytest.raises(ValueError):
            cloud.create_backup_schedule(CID, bad)
    with pytest.raises(ValueError):
        cloud.create_backup_schedule(CID, "0 3 * * *", retention_days=0)
