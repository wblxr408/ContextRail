from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from contextrail.evaluation.agent_runtime import OpenHandsServerRuntime
from contextrail.evaluation.external_agents import ExternalModelProfile, run_openhands, run_sweagent


def test_sweagent_adapter_invokes_official_cli_and_never_persists_key():
    profile = ExternalModelProfile("https://api.example/v1", "swe-secret", "openai/example")
    fake_process = MagicMock()
    fake_process.stdout = ["starting\n", "key=swe-secret\n", "submitted patch\n"]
    fake_process.wait.return_value = 0
    with tempfile.TemporaryDirectory() as folder, patch(
        "contextrail.evaluation.external_agents.subprocess.Popen", return_value=fake_process
    ) as popen:
        result = run_sweagent(
            profile=profile,
            repository_url="https://github.com/SWE-agent/test-repo",
            issue_url="https://github.com/SWE-agent/test-repo/issues/1",
            output_dir=Path(folder) / "run",
            cost_limit=2,
            log=lambda _line: None,
            executable="sweagent",
        )
        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        stored = (Path(folder) / "run" / "external-result.json").read_text(encoding="utf-8")
        output = (Path(folder) / "run" / "sweagent.stdout.log").read_text(encoding="utf-8")
    assert command[0:2] == ["sweagent", "run"]
    assert "--agent.model.name=openai/example" in command
    assert environment["OPENAI_API_KEY"] == "swe-secret"
    assert environment["PYTHONUTF8"] == "1"
    assert "swe-secret" not in stored
    assert "swe-secret" not in output
    assert result.status == "complete"


def test_openhands_adapter_uses_native_conversation_then_event_contract():
    profile = ExternalModelProfile("https://api.example/v1", "llm-secret", "openai/example")
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, url, server_key, payload=None, **_kwargs):
        calls.append((method, url, payload))
        if url.endswith("/openapi.json"):
            return {"info": {"title": "OpenHands Agent Server", "version": "test"}, "paths": {"/api/conversations": {}}}
        if method == "POST" and url.endswith("/api/conversations"):
            return {"id": "conversation-1"}
        if method == "GET" and url.endswith("/api/conversations/conversation-1"):
            return {"id": "conversation-1", "execution_status": "IDLE", "agent": {"llm": {"api_key": "llm-secret"}}}
        if url.endswith("/events/search?limit=100"):
            return {"items": [{"kind": "MessageEvent", "content": "done"}], "next_page_id": None}
        if url.endswith("/agent_final_response"):
            return {"response": "fixed"}
        return {"success": True}

    with tempfile.TemporaryDirectory() as folder, patch(
        "contextrail.evaluation.external_agents._request_json", side_effect=fake_request
    ):
        result = run_openhands(
            profile=profile,
            server_url="http://127.0.0.1:8000",
            server_api_key="server-secret",
            workspace="workspace/project",
            task="Fix the test repository.",
            output_dir=Path(folder) / "run",
            max_iterations=10,
            max_runtime_seconds=10,
            log=lambda _line: None,
        )
        stored = (Path(folder) / "run" / "openhands-conversation.json").read_text(encoding="utf-8")
    create = next(payload for method, url, payload in calls if method == "POST" and url.endswith("/api/conversations"))
    event = next(payload for method, url, payload in calls if method == "POST" and url.endswith("/events"))
    assert create["agent"]["kind"] == "Agent"
    assert create["agent"]["llm"]["base_url"] == "https://api.example/v1"
    assert create["workspace"]["kind"] == "LocalWorkspace"
    assert event["run"] is True
    assert "llm-secret" not in stored
    assert result.details["conversation_id"] == "conversation-1"


def test_local_openhands_runtime_starts_official_module_from_dedicated_venv():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        python = root / ".openhands-venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 1234
        process.stdout = []
        runtime = OpenHandsServerRuntime(root)
        with patch("contextrail.evaluation.agent_runtime.subprocess.Popen", return_value=process) as popen, patch.object(
            OpenHandsServerRuntime, "_healthy", return_value=True
        ):
            status = runtime.start(port=8123)
            command = popen.call_args.args[0]
            environment = popen.call_args.kwargs["env"]
        assert command == [str(python), "-m", "openhands.agent_server", "--host", "127.0.0.1", "--port", "8123"]
        assert environment["OH_TELEMETRY_EXPORTER"] == "none"
        assert status.url == "http://127.0.0.1:8123"
        assert status.installable is True
