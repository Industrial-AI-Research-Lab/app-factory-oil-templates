import json
from copy import deepcopy
from pathlib import Path

import pytest

from config.bundle_service import apply_import, dry_run_import
from orchestration.validators import ValidatorRunner
from tests.conftest import MockStorageBackend
from tests.research_contract_fixtures import analysis_context


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments" / "deep-domain-research" / "config"


def _load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def test_cfg01_agents_match_least_privilege_matrix():
    agents = {item["_id"]: item for item in _load("mcp-agents.json")["agents"]}
    expected = {
        "web_researcher": (
            {"attachment_presign_get", "attachment_presign_put"},
            {
                "d2_search_web",
                "d2_extract_web_pages",
                "d2_read_web_fragments",
                "d2_finalize_news_result",
            },
        ),
        "web_reviewer": (
            {"attachment_presign_get"},
            {"d2_read_web_fragments"},
        ),
        "ontology_designer": (
            {
                "attachment_list",
                "attachment_presign_get",
                "attachment_presign_put",
                "ask_human",
            },
            {
                "d2_index_documents",
                "d2_search_documents",
                "d2_read_document_fragments",
                "d2_validate_ontology",
            },
        ),
        "collection_builder": (
            {"attachment_presign_get", "attachment_presign_put"},
            {
                "d2_search_documents",
                "d2_read_document_fragments",
                "d2_read_ontology",
                "d2_validate_fact_batch",
                "d2_build_collection",
                "d2_inspect_collection",
            },
        ),
        "collection_analyst": (
            {"attachment_presign_get", "attachment_presign_put"},
            {"d2_inspect_collection", "d2_query_collection"},
        ),
        "report_builder": (
            {"attachment_presign_get", "attachment_presign_put"},
            {"r_render_report"},
        ),
    }

    assert set(agents) == set(expected)
    for agent_id, (builtins, mcp_tools) in expected.items():
        agent = agents[agent_id]
        assert agent["type"] == "generic"
        assert agent["agent_class"] == "GenericAgent"
        assert agent["enabled"] is True
        assert agent["system_prompt"].strip()
        assert set(agent["allowed_tools"]) == builtins
        assert set(agent["allowed_mcp_tools"]) == mcp_tools
        assert {"bash", "read", "create", "attachment_fetch"}.isdisjoint(
            agent["allowed_tools"]
        )


def test_cfg01_workflow_requires_approved_manifest_and_ontology():
    workflow = _load("knowledge-collection-workflow.json")
    nodes = {node["id"]: node for node in workflow["nodes"]}
    edges = {
        (edge["from"], edge["to"], edge.get("condition")) for edge in workflow["edges"]
    }

    assert workflow["execution_mode"] == "static"
    assert nodes["approve_manifest"]["type"] == "approval_gate"
    assert set(nodes["approve_manifest"]["show_keys"]) == {
        "user_prompt",
        "project_attachments",
    }
    assert (
        "approve_manifest",
        "design_ontology",
        "approved",
    ) in edges
    assert nodes["approve_ontology"]["type"] == "approval_gate"
    assert nodes["approve_ontology"]["show_keys"] == ["ontology_result"]
    assert (
        "approve_ontology",
        "build_collection",
        "approved",
    ) in edges
    assert (
        "approve_ontology",
        "design_ontology",
        "rejected",
    ) in edges
    assert set(nodes["build_collection"]["reads"]) >= {
        "ontology_result",
        "workflow_approval",
    }
    assert nodes["build_collection"]["agent_type"] == "collection_builder"


def test_cfg01_bundle_matches_source_configs():
    bundle = _load("mcp-config-bundle.json")
    assert bundle["version"] == 1
    assert bundle["items"]["agents"] == _load("mcp-agents.json")["agents"]
    assert bundle["items"]["workflows"] == [
        _load("knowledge-collection-workflow.json"),
        _load("news-research-workflow.json"),
    ]
    assert bundle["items"]["tools"] == []
    assert bundle["items"]["run_configurations"] == []
    assert bundle["items"]["a2a_servers"] == []


def test_collection_builder_prompt_uses_fact_schema_and_manifest_coverage():
    agents = {item["_id"]: item for item in _load("mcp-agents.json")["agents"]}
    prompt = agents["collection_builder"]["system_prompt"]

    assert "kind='entity'" in prompt
    assert "kind='relation'" in prompt
    assert "kind='observation'" in prompt
    assert "type_id" in prompt
    assert "source_entity_id" in prompt
    assert "subject_entity_id" in prompt
    assert "evidence:{source_id,fragment_id,text}" in prompt
    assert "каждый source_id" in prompt


@pytest.mark.asyncio
async def test_cfg01_rejects_collection_missing_approved_source():
    workflow = _load("knowledge-collection-workflow.json")
    node = next(node for node in workflow["nodes"] if node["id"] == "collection_ready")
    context = {
        "ontology_result": {
            "manifest": [
                {"attachment_id": "source-a", "filename": "a.jsonl"},
                {"attachment_id": "source-b", "filename": "b.jsonl"},
            ]
        },
        "collection_result": {
            "status": "ok",
            "approved_ontology": {},
            "source_coverage": [
                {
                    "source_id": "source-a",
                    "fragment_ids": ["a" * 64],
                    "fact_count": 1,
                    "status": "facts",
                }
            ],
            "fact_batches": [{}, {}],
            "collection": {
                "attachment_id": "collection",
                "filename": "collection.sqlite3",
                "sha256": "c" * 64,
                "counts": {},
                "fingerprints": {},
            },
            "inspection": {},
        },
    }

    result = await ValidatorRunner().run(node["checks"], context)

    assert result["passed"] is False
    assert any(
        detail["key"] == "collection_result.source_coverage"
        for detail in result["error_details"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_path",
    [
        ("report_data", "result_version"),
        ("report_data", "metadata", "schema_version"),
        ("report_data", "metadata", "collection_version"),
    ],
)
async def test_cfg01_analysis_validator_requires_renderer_version_strings(field_path):
    workflow = _load("knowledge-collection-workflow.json")
    node = next(node for node in workflow["nodes"] if node["id"] == "analysis_ready")
    report_data = {
        "project_id": "project-1",
        "run_id": "run-1",
        "result_id": "result-1",
        "result_version": "1",
        "title": "Knowledge report",
        "answer": "Answer",
        "tables": [],
        "sources": [],
        "limitations": [],
        "metadata": {
            "question": "Question",
            "schema_version": "1",
            "collection_version": "3",
        },
    }
    valid = analysis_context()
    report_data["answers"] = deepcopy(valid["analysis_result"]["answers"])
    valid["analysis_result"]["report_data"] = report_data

    accepted = await ValidatorRunner().run(node["checks"], valid)
    assert accepted["passed"] is True

    rejected = deepcopy(valid)
    target = rejected["analysis_result"]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = 1

    result = await ValidatorRunner().run(node["checks"], rejected)

    assert result["passed"] is False
    assert "schema violation" in result["errors"][0]


@pytest.mark.asyncio
async def test_cfg01_bundle_dry_run_and_apply():
    storage = MockStorageBackend()
    await storage.save_tenant(
        {"_id": "cfg01_test", "name": "CFG01 Test", "enabled": True}
    )
    storage.db.system_info.find_one.return_value = {
        "_id": "models_cache",
        "models": [
            {
                "id": "openai/gpt-5.6-terra",
                "supported_parameters": [
                    "reasoning_effort",
                    "temperature",
                    "tools",
                ],
            }
        ],
    }
    bundle = _load("mcp-config-bundle.json")

    preview = await dry_run_import(storage, bundle, "cfg01_test")
    errors = [
        {"kind": kind, "id": item.get("_id"), "error": item.get("error")}
        for kind, rows in preview["items"].items()
        for item in rows
        if item["action"] == "error"
    ]
    assert errors == [], errors

    result = await apply_import(
        storage,
        bundle,
        "cfg01_test",
        actor_id="cfg01_test",
    )
    assert result["summary"]["agents"]["insert"] == 6
    assert result["summary"]["workflows"]["insert"] == 2

    source_agents = {agent["_id"]: agent for agent in bundle["items"]["agents"]}
    for item in result["items"]["agents"]:
        saved = await storage.get_agent_configuration(item["target_id"])
        source = source_agents[item["_id"]]
        assert saved["allowed_tools"] == source["allowed_tools"]
        assert saved["allowed_mcp_tools"] == source["allowed_mcp_tools"]
