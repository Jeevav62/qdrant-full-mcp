import json
import httpx
import pytest

from qdrant_full_mcp import cloud, common, data

ACCT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CID = "11111111-1111-1111-1111-111111111111"
PKG = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    from qdrant_full_mcp import keystore
    monkeypatch.setenv("QDRANT_MCP_KEY_FILE", str(tmp_path / "keys.json"))  # never touch the real key file
    monkeypatch.setattr(keystore, "_session", {})
    monkeypatch.setenv("QDRANT_CLOUD_API_KEY", "k")
    monkeypatch.setenv("QDRANT_CLOUD_ACCOUNT_ID", ACCT)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "dbkey")
    monkeypatch.delenv("QDRANT_MCP_READ_ONLY", raising=False)
    monkeypatch.delenv("QDRANT_CLOUD_READ_ONLY", raising=False)
    monkeypatch.delenv("COLLECTION_NAME", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    for var in ("QDRANT_LOCAL_PATH", "QDRANT_READ_ONLY", "QDRANT_SEARCH_LIMIT", "FASTMCP_SERVER_HOST",
                "FASTMCP_SERVER_PORT", "FASTMCP_HOST", "FASTMCP_PORT", "QDRANT_MCP_TRANSPORT"):
        monkeypatch.delenv(var, raising=False)


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


# ------------------------------------------------------------------ text tools


@pytest.fixture
def fake_embed(monkeypatch):
    from qdrant_full_mcp import text
    seen = []

    def fake(texts, model=None):
        seen.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(text, "embed", fake)
    return seen


def test_store_text_dry_run_does_not_embed_or_write(calls, fake_embed):
    from qdrant_full_mcp import text
    out = text.store_text(["hello"], collection="notes")
    assert out["dry_run"] and out["vector_name"] == "fast-all-minilm-l6-v2"
    assert not fake_embed and not calls


def test_store_text_creates_collection_and_writes_named_vector(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/notes/exists")] = {"result": {"exists": False}}
    out = text.store_text(["hello", "world"], collection="notes", metadata=[{"a": 1}, {"a": 2}], confirm=True)
    assert out["stored"] == 2 and out["collection_created"] is True
    create = [c for c in calls if c[0] == "PUT" and c[1].endswith("/collections/notes")][0]
    assert create[2]["json"]["vectors"] == {"fast-all-minilm-l6-v2": {"size": 3, "distance": "Cosine"}}
    pts = [c for c in calls if c[1].endswith("/points")][0][2]["json"]["points"]
    assert pts[0]["payload"] == {"document": "hello", "metadata": {"a": 1}}
    assert set(pts[0]["vector"]) == {"fast-all-minilm-l6-v2"}


def test_store_text_into_unnamed_collection_uses_plain_vector(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/notes/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/notes")] = {"result": {"config": {"params": {
        "vectors": {"size": 3, "distance": "Cosine"}}}}}
    text.store_text(["hello"], collection="notes", confirm=True)
    pts = [c for c in calls if c[1].endswith("/points")][0][2]["json"]["points"]
    assert pts[0]["vector"] == [0.1, 0.2, 0.3]


def test_store_text_validation(calls, fake_embed):
    from qdrant_full_mcp import text
    with pytest.raises(ValueError):
        text.store_text([], collection="notes")
    with pytest.raises(ValueError):
        text.store_text(["a", "b"], collection="notes", metadata=[{}])
    with pytest.raises(ValueError):
        text.store_text(["a"], collection="notes", ids=[1, 2])
    with pytest.raises(ValueError):
        text.store_text(["a"], collection="a/b", confirm=True)


def test_store_text_blocked_in_read_only(calls, fake_embed, monkeypatch):
    from qdrant_full_mcp import text
    monkeypatch.setenv("QDRANT_MCP_READ_ONLY", "1")
    with pytest.raises(ValueError, match="read-only"):
        text.store_text(["hello"], collection="notes", confirm=True)
    assert not calls


def test_find_text_queries_named_vector_and_shapes_result(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/notes/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/notes")] = {"result": {"config": {"params": {"vectors": {
        "fast-all-minilm-l6-v2": {"size": 3, "distance": "Cosine"}}}}}}
    calls.routes[("POST", "/collections/notes/points/query")] = {"result": {"points": [
        {"id": "1", "score": 0.9, "payload": {"document": "hello", "metadata": {"a": 1}}}]}}
    hits = text.find_text("hi", collection="notes", limit=3, filter={"must": []})
    assert hits == [{"id": "1", "score": 0.9, "document": "hello", "metadata": {"a": 1}}]
    body = [c for c in calls if c[1].endswith("/points/query")][0][2]["json"]
    assert body["using"] == "fast-all-minilm-l6-v2" and body["limit"] == 3 and body["query"] == [0.1, 0.2, 0.3]


def test_find_text_missing_collection(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/nope/exists")] = {"result": {"exists": False}}
    with pytest.raises(ValueError, match="does not exist"):
        text.find_text("hi", collection="nope")


def test_find_text_rejects_foreign_vector_layout(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/c/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/c")] = {"result": {"config": {"params": {"vectors": {
        "dense": {"size": 3}, "other": {"size": 3}}}}}}
    with pytest.raises(ValueError, match="none is"):
        text.find_text("hi", collection="c")


def test_missing_fastembed_gives_install_hint(monkeypatch):
    import builtins
    from qdrant_full_mcp import text
    real = builtins.__import__

    def no_fastembed(name, *a, **kw):
        if name == "fastembed":
            raise ImportError
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_fastembed)
    monkeypatch.setattr(text, "_models", {})
    with pytest.raises(common.QdrantError, match="pip install fastembed"):
        text.embed(["x"])


def test_store_text_rejects_dimension_mismatch_before_writing(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/notes/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/notes")] = {"result": {"config": {"params": {
        "vectors": {"size": 768, "distance": "Cosine"}}}}}
    with pytest.raises(ValueError, match="768-dimension"):
        text.store_text(["hello"], collection="notes", confirm=True)
    assert not writes(calls)


def test_store_text_checks_layout_before_embedding(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/c/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/c")] = {"result": {"config": {"params": {"vectors": {
        "dense": {"size": 3}, "other": {"size": 3}}}}}}
    with pytest.raises(ValueError, match="none is"):
        text.store_text(["hello"], collection="c", confirm=True)
    assert not fake_embed


def test_text_module_alone_registers_only_text_tools():
    import os, subprocess, sys
    code = ("from qdrant_full_mcp import server; server.load_modules(); "
            "print(sorted(t.name for t in server.mcp._tool_manager.list_tools()))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "QDRANT_MCP_MODULES": "text"}).stdout
    assert out.strip() == "['find_text', 'store_text']"


def test_default_collection_stores_in_one_step(calls, fake_embed, monkeypatch):
    from qdrant_full_mcp import text
    monkeypatch.setenv("COLLECTION_NAME", "memory")
    calls.routes[("GET", "/collections/memory/exists")] = {"result": {"exists": False}}
    out = text.store_text("remember this", metadata={"source": "chat"})
    assert out["stored"] == 1 and out["collection"] == "memory"
    pts = [c for c in calls if c[1].endswith("/collections/memory/points")][0][2]["json"]["points"]
    assert pts[0]["payload"] == {"document": "remember this", "metadata": {"source": "chat"}}


def test_other_collection_still_dry_runs_with_default_set(calls, fake_embed, monkeypatch):
    from qdrant_full_mcp import text
    monkeypatch.setenv("COLLECTION_NAME", "memory")
    assert text.store_text("x", collection="other")["dry_run"]
    assert not calls and not fake_embed


def test_dict_metadata_applies_to_every_text(calls, fake_embed):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/n/exists")] = {"result": {"exists": False}}
    text.store_text(["a", "b"], collection="n", metadata={"t": 1}, confirm=True)
    pts = [c for c in calls if c[1].endswith("/collections/n/points")][0][2]["json"]["points"]
    assert [p["payload"]["metadata"] for p in pts] == [{"t": 1}, {"t": 1}]


def test_find_text_uses_default_collection(calls, fake_embed, monkeypatch):
    from qdrant_full_mcp import text
    monkeypatch.setenv("COLLECTION_NAME", "memory")
    calls.routes[("GET", "/collections/memory/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/memory")] = {"result": {"config": {"params": {"vectors": {
        "fast-all-minilm-l6-v2": {"size": 3, "distance": "Cosine"}}}}}}
    calls.routes[("POST", "/collections/memory/points/query")] = {"result": {"points": []}}
    assert text.find_text("anything") == []


def test_text_tools_need_a_collection(calls, fake_embed):
    from qdrant_full_mcp import text
    with pytest.raises(ValueError, match="COLLECTION_NAME"):
        text.store_text("x", confirm=True)
    with pytest.raises(ValueError, match="COLLECTION_NAME"):
        text.find_text("x")


def test_transport_args(monkeypatch):
    from qdrant_full_mcp import server
    monkeypatch.delenv("QDRANT_MCP_TRANSPORT", raising=False)
    assert server.parse_args([]).transport == "stdio"
    a = server.parse_args(["--transport", "streamable-http", "--port", "9000"])
    assert (a.transport, a.host, a.port) == ("streamable-http", "127.0.0.1", 9000)
    monkeypatch.setenv("QDRANT_MCP_TRANSPORT", "sse")
    assert server.parse_args([]).transport == "sse"
    monkeypatch.setenv("QDRANT_MCP_TRANSPORT", "bogus")
    with pytest.raises(SystemExit):
        server.parse_args([])



# ------------------------------------------------------- official-name parity


def test_official_read_only_name_blocks_writes(calls, monkeypatch):
    monkeypatch.setenv("QDRANT_READ_ONLY", "true")
    with pytest.raises(ValueError, match="read-only"):
        data.upsert_points("c", [{"id": 1, "vector": [0.1]}], confirm=True)
    assert not calls


def test_official_host_port_names(monkeypatch):
    from qdrant_full_mcp import server
    monkeypatch.setenv("FASTMCP_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("FASTMCP_SERVER_PORT", "9100")
    monkeypatch.setenv("FASTMCP_HOST", "10.0.0.1")
    a = server.parse_args([])
    assert (a.host, a.port) == ("0.0.0.0", 9100)


def test_non_loopback_http_drops_localhost_host_pin(monkeypatch, capsys):
    from qdrant_full_mcp import server
    monkeypatch.setattr(server.mcp.settings, "transport_security", "pinned")
    server.configure_http("127.0.0.1", 8000)
    assert server.mcp.settings.transport_security == "pinned"
    server.configure_http("0.0.0.0", 8000)
    assert server.mcp.settings.transport_security is None
    assert "without authentication" in capsys.readouterr().err


def test_search_limit_env(calls, fake_embed, monkeypatch):
    from qdrant_full_mcp import text
    calls.routes[("GET", "/collections/n/exists")] = {"result": {"exists": True}}
    calls.routes[("GET", "/collections/n")] = {"result": {"config": {"params": {"vectors": {
        "fast-all-minilm-l6-v2": {"size": 3}}}}}}
    calls.routes[("POST", "/collections/n/points/query")] = {"result": {"points": []}}
    text.find_text("q", collection="n")
    monkeypatch.setenv("QDRANT_SEARCH_LIMIT", "12")
    text.find_text("q", collection="n")
    text.find_text("q", collection="n", limit=2)
    limits = [c[2]["json"]["limit"] for c in calls if c[1].endswith("/points/query")]
    assert limits == [5, 12, 2]
    monkeypatch.setenv("QDRANT_SEARCH_LIMIT", "lots")
    with pytest.raises(ValueError, match="QDRANT_SEARCH_LIMIT"):
        text.find_text("q", collection="n")


def test_custom_tool_descriptions():
    import os, subprocess, sys
    code = ("from qdrant_full_mcp import server; server.load_modules(); "
            "t = {x.name: x.description for x in server.mcp._tool_manager.list_tools()}; "
            "print(t['store_text']); print(t['find_text'])")
    env = {**os.environ, "QDRANT_MCP_MODULES": "text", "TOOL_STORE_DESCRIPTION": "Save a code snippet.",
           "TOOL_FIND_DESCRIPTION": "Find a code snippet."}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env).stdout
    assert out.splitlines() == ["Save a code snippet.", "Find a code snippet."]


# -------------------------------------------------------------- local mode


@pytest.fixture
def local_mode(monkeypatch):
    from qdrant_full_mcp import local
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setenv("QDRANT_LOCAL_PATH", ":memory:")
    monkeypatch.setattr(local, "_client", None)
    yield local
    if local._client is not None:
        local._client.close()


def test_local_mode_data_tools_without_http(local_mode, calls):
    data.create_collection("c", vector_size=2, confirm=True)
    data.upsert_points("c", [{"id": 1, "vector": [1, 0], "payload": {"k": "a"}},
                             {"id": 2, "vector": [0, 1], "payload": {"k": "b"}}], confirm=True)
    assert data.count_points("c")["count"] == 2
    assert data.query_points("c", vector=[0.1, 0.9], limit=1)["points"][0]["id"] == 2
    assert data.scroll_points("c", filter={"must": [{"key": "k", "match": {"value": "a"}}]})["points"][0]["id"] == 1
    data.delete_points("c", ids=[1], confirm=True)
    assert data.count_points("c")["count"] == 1
    data.delete_collection("c", "c")
    assert data.list_collections() == {"collections": []}
    assert not calls  # nothing went over HTTP


def test_local_mode_text_tools(local_mode, fake_embed, monkeypatch):
    from qdrant_full_mcp import text
    monkeypatch.setenv("COLLECTION_NAME", "mem")
    out = text.store_text("hello", metadata={"a": 1})
    assert out["collection_created"] is True
    hits = text.find_text("hi")
    assert hits[0]["document"] == "hello" and hits[0]["metadata"] == {"a": 1}


def test_local_mode_errors(local_mode, monkeypatch):
    with pytest.raises(ValueError, match="does not exist"):
        data.get_collection("missing")
    monkeypatch.setenv("QDRANT_URL", "http://x")
    with pytest.raises(ValueError, match="not both"):
        data.list_collections()


def test_local_mode_unsupported_route(local_mode):
    from qdrant_full_mcp import local
    with pytest.raises(common.QdrantError, match="Not available in local mode"):
        local.request("POST", "/collections/c/points/recommend", {})


def test_missing_index_error_explains_the_fix(monkeypatch):
    def strict(method, url, **kw):
        return httpx.Response(400, json={"status": {"error": 'Bad request: Index required but not found for '
                              '"metadata.topic" of one of the following types: [keyword]. Help: Create an index'}},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(common.httpx, "request", strict)
    with pytest.raises(ValueError, match=r"create_payload_index with field_name='metadata.topic', field_schema='keyword'"):
        data.count_points("c", filter={"must": [{"key": "metadata.topic", "match": {"value": "x"}}]})



# ------------------------------------------------------ remembered db keys

KEY_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def cluster_routes(calls, monkeypatch):
    common._endpoint_cache.clear()
    monkeypatch.delenv("QDRANT_API_KEY")
    calls.routes[("GET", f"/clusters/{CID}")] = {
        "cluster": {"state": {"endpoint": {"url": "https://abc.cloud.qdrant.io", "restPort": 6333}}}}
    calls.routes[("POST", "/database-api-keys")] = {"databaseApiKey": {"id": KEY_ID, "key": "s3cret"}}
    return calls


def test_created_key_works_immediately_without_saving(cluster_routes, tmp_path):
    r = cloud.create_database_api_key(CID, "app key", access="manage", confirm=True)
    assert r["remembered"]["mode"] == "session" and r["remembered"]["key_file"] is None
    data.list_collections(cluster_id=CID)
    assert cluster_routes[-1][2]["headers"]["api-key"] == "s3cret"
    assert not (tmp_path / "keys.json").exists()


def test_saved_key_survives_a_restart(cluster_routes, tmp_path):
    from qdrant_full_mcp import keystore
    r = cloud.create_database_api_key(CID, "app key", remember="save", confirm=True)
    saved = json.loads((tmp_path / "keys.json").read_text())
    assert saved[CID]["key"] == "s3cret" and saved[CID]["key_id"] == KEY_ID
    assert r["remembered"]["key_file"] == str(tmp_path / "keys.json")
    keystore._session.clear()  # new process
    data.list_collections(cluster_id=CID)
    assert cluster_routes[-1][2]["headers"]["api-key"] == "s3cret"
    listed = cloud.list_remembered_keys()["keys"]
    assert listed == [{"cluster_id": CID, "key_id": KEY_ID, "name": "app key", "where": "saved",
                       "saved_at": saved[CID]["saved_at"]}]
    assert "s3cret" not in json.dumps(cloud.list_remembered_keys())


def test_remember_none_and_dry_run_keep_nothing(cluster_routes):
    from qdrant_full_mcp import keystore
    assert cloud.create_database_api_key(CID, "app key", remember="save")["dry_run"]
    cloud.create_database_api_key(CID, "app key", remember="none", confirm=True)
    assert keystore.get(CID) is None
    with pytest.raises(ValueError):
        cloud.create_database_api_key(CID, "app key", remember="forever")


def test_env_key_wins_over_remembered(cluster_routes, monkeypatch):
    cloud.create_database_api_key(CID, "app key", confirm=True)
    monkeypatch.setenv("QDRANT_API_KEY_" + CID.replace("-", "_").upper(), "fromenv")
    data.list_collections(cluster_id=CID)
    assert cluster_routes[-1][2]["headers"]["api-key"] == "fromenv"


def test_deleting_key_or_cluster_forgets_it(cluster_routes, tmp_path):
    from qdrant_full_mcp import keystore
    cloud.create_database_api_key(CID, "app key", remember="save", confirm=True)
    cloud.delete_database_api_key(CID, "44444444-4444-4444-4444-444444444444", confirm=True)  # other key
    assert keystore.get(CID) == "s3cret"
    r = cloud.delete_database_api_key(CID, KEY_ID, confirm=True)
    assert r["forgotten_locally"] == {"session": True, "saved": True} and keystore.get(CID) is None
    cloud.create_database_api_key(CID, "app key", remember="save", confirm=True)
    cluster_routes.routes[("GET", f"/clusters/{CID}")]["cluster"]["name"] = "tmp"
    cloud.delete_cluster(CID, confirm_cluster_name="tmp")
    assert keystore.get(CID) is None and json.loads((tmp_path / "keys.json").read_text()) == {}


def test_forget_tool_does_not_call_qdrant(cluster_routes):
    from qdrant_full_mcp import keystore
    cloud.create_database_api_key(CID, "app key", confirm=True)
    n = len(cluster_routes)
    assert cloud.forget_database_api_key(CID)["forgotten"]["session"] is True
    assert keystore.get(CID) is None and len(cluster_routes) == n


def test_rejected_remembered_key_explains(cluster_routes, monkeypatch):
    cloud.create_database_api_key(CID, "app key", confirm=True)
    real = common.httpx.request

    def deny(method, url, **kw):
        if "abc.cloud.qdrant.io" in url:
            return httpx.Response(401, json={"status": {"error": "Unauthorized"}}, request=httpx.Request(method, url))
        return real(method, url, **kw)
    monkeypatch.setattr(common.httpx, "request", deny)
    with pytest.raises(ValueError, match="remembered key for this cluster was rejected"):
        data.list_collections(cluster_id=CID)


def test_key_file_off_disables_saving(cluster_routes, monkeypatch):
    monkeypatch.setenv("QDRANT_MCP_KEY_FILE", "off")
    r = cloud.create_database_api_key(CID, "app key", remember="save", confirm=True)
    assert "disabled" in r["remembered"]["error"] and r["remembered"]["mode"] == "session"
    data.list_collections(cluster_id=CID)  # still usable this session
    assert cluster_routes[-1][2]["headers"]["api-key"] == "s3cret"
