from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from rosclaw_know.api import app, configure_v2_store
from rosclaw_know.store import InMemoryKnowStore


def test_v2_capabilities_plan_reference_pack_and_feedback_contracts():
    store = InMemoryKnowStore()
    configure_v2_store(store)
    try:
        with TestClient(app) as client:
            health = client.get("/know/v2/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["schema_version"] == "know.v2"
            assert health.json()["source_count"] == 0

            capabilities = client.get("/know/v2/capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json()["store"]["backend"] == "memory"
            assert "rosclaw.know.reference_pack.v2" in capabilities.json()["schema_versions"]

            plan = client.post(
                "/know/v2/research/plan",
                json={
                    "request_id": "research-1",
                    "topic": "Unitree G1 football",
                    "goal": "find pinned implementation references",
                },
            )
            assert plan.status_code == 200, plan.text
            assert plan.json()["subquestions"]

            response = client.post(
                "/know/v2/reference-packs",
                json={
                    "query": "E42_TIMEOUT",
                    "context": {"robot": "unitree_g1"},
                    "top_k": 5,
                    "token_budget": 1000,
                },
            )
            assert response.status_code == 200, response.text
            pack = response.json()
            assert pack["schema_version"] == "rosclaw.know.reference_pack.v2"
            assert pack["items"] == []
            for alias in ("/know/v2/reference-packs/build", "/know/v2/retrieve"):
                alias_response = client.post(
                    alias,
                    json={
                        "query": "E42_TIMEOUT",
                        "context": {"robot": "unitree_g1"},
                        "top_k": 5,
                        "token_budget": 1000,
                    },
                )
                assert alias_response.status_code == 200, alias_response.text
                assert alias_response.json()["reference_pack_id"] == pack["reference_pack_id"]
            fetched = client.get(f"/know/v2/reference-packs/{pack['reference_pack_id']}")
            assert fetched.status_code == 200

            feedback = {
                "feedback_id": "feedback-1",
                "reference_pack_id": pack["reference_pack_id"],
                "knowledge_unit_id": "unit-missing",
                "verdict": "unknown",
                "context_hash": "a" * 64,
                "origin": "agent",
                "created_at": datetime.now(UTC).isoformat(),
            }
            first = client.post("/know/v2/feedback", json=feedback)
            second = client.post("/know/v2/feedback", json=feedback)
            assert first.status_code == 201, first.text
            assert first.json()["governance"]["queue"] == "manual_review"
            assert first.json()["governance"]["automatic_mutation_allowed"] is False
            assert second.status_code == 200, second.text
            assert second.json()["created"] is False
            governance = client.get(
                "/know/v2/feedback/governance", params={"status": "pending_review"}
            )
            assert governance.status_code == 200, governance.text
            assert governance.json()["count"] == 1
            assert governance.json()["automatic_mutation_allowed"] is False

            assert client.get("/know/v2/sources/missing").status_code == 404
            assert client.get("/know/v2/snapshots/missing").status_code == 404
            assert client.get("/know/v2/projects/missing").status_code == 404
            assert client.get("/know/v2/wiki/pages/missing").status_code == 404
            assert client.get("/know/v2/evidence/missing").status_code == 404
    finally:
        configure_v2_store(None)


def test_v2_content_endpoints_honor_optional_api_key(monkeypatch):
    store = InMemoryKnowStore()
    configure_v2_store(store)
    monkeypatch.setenv("ROSCLAW_KNOW_API_KEYS", "know-test-key")
    try:
        with TestClient(app) as client:
            assert client.get("/know/v2/health").status_code == 200
            payload = {
                "query": "bounded fixture",
                "context": {"task": "auth test"},
            }
            assert client.post("/know/v2/reference-packs", json=payload).status_code == 401
            assert (
                client.post(
                    "/know/v2/reference-packs",
                    json=payload,
                    headers={"X-API-Key": "wrong"},
                ).status_code
                == 403
            )
            accepted = client.post(
                "/know/v2/reference-packs",
                json=payload,
                headers={"X-API-Key": "know-test-key"},
            )
            assert accepted.status_code == 200
    finally:
        configure_v2_store(None)
