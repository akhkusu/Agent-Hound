import json
import zipfile

from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.output.ad_bridge import build_ad_bridge


USER_SID = "S-1-5-21-1-2-3-1104"
COMPUTER_SID = "S-1-5-21-1-2-3-1162"


def _agent_graph(computer_sid=COMPUTER_SID):
    return {"graph": {"nodes": [{
        "id": "agent-test",
        "kinds": ["Agent"],
        "properties": {
            "runtime_owner_sid": USER_SID,
            "runtime_computer_sid": computer_sid,
            "runtime_observed_at": "2026-10-06T08:34:05+00:00",
        },
    }], "edges": []}}


def _ad_zip(path, user_sid=USER_SID, computer_sid=COMPUTER_SID):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("test_users.json", json.dumps({"data": [{"ObjectIdentifier": user_sid}]}))
        archive.writestr("test_computers.json", json.dumps({"data": [{"ObjectIdentifier": computer_sid}]}))


def test_bridge_matches_both_sids(tmp_path):
    archive = tmp_path / "ad.zip"
    _ad_zip(archive)
    edges = build_ad_bridge(_agent_graph(), archive)["graph"]["edges"]
    assert [edge["kind"] for edge in edges] == ["AH_RunsAs", "AH_RunsOn"]
    assert [edge["end"]["value"] for edge in edges] == [USER_SID, COMPUTER_SID]
    assert all(edge["properties"]["confidence"] == "confirmed" for edge in edges)


def test_bridge_omits_unmatched_and_unobserved_host(tmp_path):
    archive = tmp_path / "ad.zip"
    _ad_zip(archive, user_sid="S-1-5-21-1-2-3-9999")
    assert build_ad_bridge(_agent_graph(computer_sid=None), archive)["graph"]["edges"] == []


def test_bridge_cli_writes_payload(tmp_path):
    archive = tmp_path / "ad.zip"
    source = tmp_path / "agent.json"
    output = tmp_path / "bridge.json"
    _ad_zip(archive)
    source.write_text(json.dumps(_agent_graph()))
    result = CliRunner().invoke(cli, ["ad-bridge", "--agent-graph", str(source), "--ad-zip", str(archive), "-o", str(output)])
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert "metadata" not in payload
    assert len(payload["graph"]["edges"]) == 2
