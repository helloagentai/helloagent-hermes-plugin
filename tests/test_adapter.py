"""Tests for the HelloAgent platform adapter plugin."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import helloagent_hermes.adapter as _hello_mod

HelloAgentAdapter = _hello_mod.HelloAgentAdapter
_env_enablement = _hello_mod._env_enablement
_resolve_token = _hello_mod._resolve_token
_standalone_send = _hello_mod._standalone_send
_write_credentials_file = _hello_mod._write_credentials_file
check_requirements = _hello_mod.check_requirements
interactive_setup = _hello_mod.interactive_setup
is_connected = _hello_mod.is_connected
register = _hello_mod.register
validate_config = _hello_mod.validate_config


class _RegistryCtx:
    def register_platform(self, **kwargs):
        from gateway.platform_registry import PlatformEntry, platform_registry

        kwargs.setdefault("source", "plugin")
        kwargs.setdefault("plugin_name", "helloagent")
        platform_registry.register(PlatformEntry(**kwargs))


register(_RegistryCtx())


@pytest.fixture(autouse=True)
def clean_helloagent_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    for key in (
        "HELLOAGENT_TOKEN",
        "HELLOAGENT_RELAY_URL",
        "HELLOAGENT_API_URL",
        "HELLOAGENT_DISPLAY_NAME",
        "HELLOAGENT_ALLOWED_USERS",
        "HELLOAGENT_ALLOW_ALL_USERS",
        "HELLOAGENT_HOME_CHANNEL",
        "HELLOAGENT_DEBUG",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def platform_config():
    from gateway.config import PlatformConfig

    return PlatformConfig(enabled=True, token="ha_config")


class FakeAuthFailedError(Exception):
    pass


class FakeWS:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeAgent:
    instances = []

    def __init__(self, token, relay_url=None, **kwargs):
        self.token = token
        self.relay_url = relay_url
        self.kwargs = kwargs
        self.handle = "alice/jarvis"
        self.ws = FakeWS()
        self.handler = None
        self.sent = []
        self._stop = asyncio.Event()
        type(self).instances.append(self)

    def on_message(self, fn):
        self.handler = fn
        return fn

    async def run(self):
        await self._stop.wait()

    async def send(self, to_handle, text, conversation_id=None):
        self.sent.append(
            {
                "to_handle": to_handle,
                "text": text,
                "conversation_id": conversation_id,
            }
        )
        return "msg_123"


def install_fake_helloagent(monkeypatch, agent_cls, auth_error_cls=FakeAuthFailedError):
    module = types.ModuleType("helloagent")
    module.Agent = agent_cls
    module.AuthFailedError = auth_error_cls
    monkeypatch.setitem(sys.modules, "helloagent", module)
    return module


@pytest.fixture
def fake_helloagent_module(monkeypatch):
    FakeAgent.instances.clear()
    return install_fake_helloagent(monkeypatch, FakeAgent)


def test_resolve_token_precedence_env_over_config(monkeypatch, platform_config):
    monkeypatch.setenv("HELLOAGENT_TOKEN", "ha_env")

    assert _resolve_token(platform_config) == "ha_env"


def test_resolve_token_uses_config_extra_when_no_config_token(monkeypatch):
    from gateway.config import PlatformConfig

    cfg = PlatformConfig(enabled=True, extra={"token": "ha_extra"})

    assert _resolve_token(cfg) == "ha_extra"
    assert validate_config(cfg) is True


def test_resolve_token_uses_credentials_file(monkeypatch, tmp_path):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    creds = tmp_path / "credentials"
    creds.mkdir(parents=True)
    (creds / "helloagent.json").write_text('{"token": "ha_file"}', encoding="utf-8")

    assert _resolve_token(PlatformConfig(enabled=True)) == "ha_file"


def test_read_credentials_file_ignores_invalid_json(monkeypatch, tmp_path):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    creds = tmp_path / "credentials"
    creds.mkdir(parents=True)
    (creds / "helloagent.json").write_text("{not json", encoding="utf-8")

    assert _resolve_token(PlatformConfig(enabled=True)) == ""
    assert validate_config(PlatformConfig(enabled=True)) is False


def test_write_credentials_file_persists_versioned_token(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    _write_credentials_file("ha_file", "@alice/jarvis")

    path = tmp_path / "credentials" / "helloagent.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["handle"] == "alice/jarvis"
    assert data["owner_handle"] == "alice"
    assert data["agent_name"] == "jarvis"
    assert data["token"] == "ha_file"
    assert data["linked_at"].endswith("Z")
    assert path.stat().st_mode & 0o777 == 0o600


def test_env_enablement_none_without_token():
    assert _env_enablement() is None


def test_env_enablement_seeds_extra_and_home(monkeypatch):
    monkeypatch.setenv("HELLOAGENT_TOKEN", "ha_env")
    monkeypatch.setenv("HELLOAGENT_RELAY_URL", "ws://relay.test/v1/ws")
    monkeypatch.setenv("HELLOAGENT_HOME_CHANNEL", "alice")

    seed = _env_enablement()

    assert seed["relay_url"] == "ws://relay.test/v1/ws"
    assert seed["api_url"] == _hello_mod.DEFAULT_API_URL
    assert seed["home_channel"] == {"chat_id": "alice", "name": "alice"}


def test_env_enablement_includes_display_name_and_allowed_users(monkeypatch):
    monkeypatch.setenv("HELLOAGENT_TOKEN", "ha_env")
    monkeypatch.setenv("HELLOAGENT_DISPLAY_NAME", "Jarvis")
    monkeypatch.setenv("HELLOAGENT_ALLOWED_USERS", "alice, bob, ")

    seed = _env_enablement()

    assert seed["display_name"] == "Jarvis"
    assert seed["allowed_users"] == ["alice", "bob"]


def test_check_requirements_true_when_sdk_importable(fake_helloagent_module):
    assert check_requirements() is True


def test_check_requirements_false_when_sdk_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "helloagent", raising=False)

    assert check_requirements() is False


def test_interactive_setup_saves_token_allowlist_and_home(monkeypatch, tmp_path):
    saved = {}
    prompts = iter(["ha_setup", "alice, bob", "alice"])

    setup_module = types.ModuleType("hermes_cli.setup")
    setup_module.get_env_value = lambda key: ""
    setup_module.print_header = lambda msg: None
    setup_module.print_info = lambda msg: None
    setup_module.print_success = lambda msg: None
    setup_module.print_warning = lambda msg: None
    setup_module.prompt = lambda *args, **kwargs: next(prompts)
    setup_module.prompt_yes_no = lambda *args, **kwargs: True
    setup_module.save_env_value = lambda key, value: saved.setdefault(key, value)
    monkeypatch.setitem(sys.modules, "hermes_cli.setup", setup_module)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    interactive_setup()

    assert saved == {
        "HELLOAGENT_TOKEN": "ha_setup",
        "HELLOAGENT_ALLOWED_USERS": "alice,bob",
        "HELLOAGENT_HOME_CHANNEL": "alice",
    }
    data = json.loads(
        (tmp_path / "credentials" / "helloagent.json").read_text(encoding="utf-8")
    )
    assert data["token"] == "ha_setup"


def test_interactive_setup_can_keep_existing_config(monkeypatch):
    setup_module = types.ModuleType("hermes_cli.setup")
    setup_module.get_env_value = lambda key: "ha_existing"
    setup_module.print_header = lambda msg: None
    setup_module.print_info = lambda msg: None
    setup_module.print_success = lambda msg: None
    setup_module.print_warning = lambda msg: None
    setup_module.prompt = lambda *args, **kwargs: pytest.fail("should not prompt")
    setup_module.prompt_yes_no = lambda *args, **kwargs: False
    setup_module.save_env_value = lambda *args, **kwargs: pytest.fail("should not save")
    monkeypatch.setitem(sys.modules, "hermes_cli.setup", setup_module)

    interactive_setup()


def test_interactive_setup_rejects_bad_token(monkeypatch):
    warnings = []

    setup_module = types.ModuleType("hermes_cli.setup")
    setup_module.get_env_value = lambda key: ""
    setup_module.print_header = lambda msg: None
    setup_module.print_info = lambda msg: None
    setup_module.print_success = lambda msg: None
    setup_module.print_warning = warnings.append
    setup_module.prompt = lambda *args, **kwargs: "bad"
    setup_module.prompt_yes_no = lambda *args, **kwargs: True
    setup_module.save_env_value = lambda *args, **kwargs: pytest.fail("should not save")
    monkeypatch.setitem(sys.modules, "hermes_cli.setup", setup_module)

    interactive_setup()

    assert warnings == ["Token must start with 'ha_'; skipping HelloAgent setup."]


class TestHelloAgentAdapter:
    def test_init_reads_env_and_allowlist(self, monkeypatch, platform_config):
        monkeypatch.setenv("HELLOAGENT_TOKEN", "ha_env")
        monkeypatch.setenv("HELLOAGENT_ALLOWED_USERS", "Alice, bob")
        monkeypatch.setenv("HELLOAGENT_RELAY_URL", "ws://relay.test/v1/ws")

        adapter = HelloAgentAdapter(platform_config)

        assert adapter._token == "ha_env"
        assert adapter._relay_url == "ws://relay.test/v1/ws"
        assert adapter._allowed_users == {"alice", "bob"}

    def test_name(self, platform_config):
        assert HelloAgentAdapter(platform_config).name == "HelloAgent"

    def test_init_reads_config_extra(self):
        from gateway.config import PlatformConfig

        cfg = PlatformConfig(
            enabled=True,
            token="ha_config",
            extra={
                "relay_url": "ws://relay.extra/v1/ws",
                "api_url": "https://api.extra",
                "display_name": "Extra Jarvis",
                "allowed_users": ["Alice", "Bob"],
            },
        )

        adapter = HelloAgentAdapter(cfg)

        assert adapter._relay_url == "ws://relay.extra/v1/ws"
        assert adapter._api_url == "https://api.extra"
        assert adapter._display_name == "Extra Jarvis"
        assert adapter._allowed_users == {"alice", "bob"}

    def test_init_accepts_string_allowlist_in_config_extra(self):
        from gateway.config import PlatformConfig

        cfg = PlatformConfig(
            enabled=True,
            token="ha_config",
            extra={"allowed_users": "Alice, Bob"},
        )

        adapter = HelloAgentAdapter(cfg)

        assert adapter._allowed_users == {"alice", "bob"}

    def test_adapter_prefilter_defaults_to_gateway(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)

        assert adapter._passes_adapter_prefilter("anyone") is True

    def test_adapter_prefilter_allows_all_with_env(self, monkeypatch, platform_config):
        monkeypatch.setenv("HELLOAGENT_ALLOWED_USERS", "alice")
        monkeypatch.setenv("HELLOAGENT_ALLOW_ALL_USERS", "true")
        adapter = HelloAgentAdapter(platform_config)

        assert adapter._passes_adapter_prefilter("bob") is True

    def test_adapter_prefilter_restricts_when_allowlist_set(self, monkeypatch, platform_config):
        monkeypatch.setenv("HELLOAGENT_ALLOWED_USERS", "alice")
        adapter = HelloAgentAdapter(platform_config)

        assert adapter._passes_adapter_prefilter("alice") is True
        assert adapter._passes_adapter_prefilter("bob") is False

    @pytest.mark.asyncio
    async def test_handle_inbound_builds_message_event(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)
        captured = []

        async def capture(event):
            captured.append(event)

        adapter._message_handler = AsyncMock(return_value="unused")
        adapter.handle_message = capture
        msg = SimpleNamespace(
            from_handle="alice",
            conversation_id="conv_1",
            message_id="m_1",
            text="hello",
        )

        await adapter._handle_inbound(msg)

        assert adapter._conversation_ids["alice"] == "conv_1"
        assert len(captured) == 1
        event = captured[0]
        assert event.text == "hello"
        assert event.message_id == "m_1"
        assert event.source.platform.value == "helloagent"
        assert event.source.chat_id == "alice"
        assert event.source.chat_type == "dm"
        assert event.source.user_id == "alice"

    @pytest.mark.asyncio
    async def test_handle_inbound_drops_missing_sender(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)
        adapter.handle_message = AsyncMock()

        await adapter._handle_inbound(SimpleNamespace(text="hello"))

        adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_inbound_drops_unauthorized_sender(
        self, monkeypatch, platform_config
    ):
        monkeypatch.setenv("HELLOAGENT_ALLOWED_USERS", "alice")
        adapter = HelloAgentAdapter(platform_config)
        adapter.handle_message = AsyncMock()

        await adapter._handle_inbound(
            SimpleNamespace(from_handle="bob", conversation_id="conv", text="hello")
        )

        adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_inbound_drops_when_gateway_not_ready(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)
        adapter.handle_message = AsyncMock()

        await adapter._handle_inbound(
            SimpleNamespace(
                from_handle="alice",
                conversation_id="conv_1",
                message_id="m_1",
                text="hello",
            )
        )

        assert adapter._conversation_ids["alice"] == "conv_1"
        adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_awaits_sdk_send_with_conversation_id(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)
        agent = SimpleNamespace(send=AsyncMock(return_value="out_1"))
        adapter._sdk_agent = agent
        adapter._handle = "owner/jarvis"
        adapter._conversation_ids["alice"] = "conv_1"

        result = await adapter.send("alice", "hi")

        assert result.success is True
        assert result.message_id == "out_1"
        agent.send.assert_awaited_once_with(
            to_handle="alice",
            text="hi",
            conversation_id="conv_1",
        )

    @pytest.mark.asyncio
    async def test_send_returns_error_when_not_connected(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)

        result = await adapter.send("alice", "hi")

        assert result.success is False
        assert result.error == "HelloAgent: not connected"

    @pytest.mark.asyncio
    async def test_send_returns_error_when_sdk_send_fails(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)
        adapter._sdk_agent = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("boom")))
        adapter._handle = "owner/jarvis"

        result = await adapter.send("alice", "hi")

        assert result.success is False
        assert result.error == "boom"

    @pytest.mark.asyncio
    async def test_get_chat_info(self, platform_config):
        adapter = HelloAgentAdapter(platform_config)

        assert await adapter.get_chat_info("alice") == {
            "chat_id": "alice",
            "name": "alice",
            "type": "dm",
        }

    @pytest.mark.asyncio
    async def test_connect_starts_agent_and_disconnect_cleans_up(
        self, fake_helloagent_module, platform_config
    ):
        adapter = HelloAgentAdapter(platform_config)

        assert await adapter.connect() is True
        assert adapter._handle == "alice/jarvis"
        assert FakeAgent.instances[0].token == "ha_config"
        assert FakeAgent.instances[0].relay_url == _hello_mod.DEFAULT_RELAY_URL
        adapter._handle_inbound = AsyncMock()

        result = await FakeAgent.instances[0].handler(SimpleNamespace(text="hi"))

        assert result is None
        adapter._handle_inbound.assert_awaited_once()

        await adapter.disconnect()
        assert adapter._sdk_agent is None
        assert FakeAgent.instances[0].ws.closed is True

    @pytest.mark.asyncio
    async def test_connect_rejects_bad_token(self, monkeypatch, fake_helloagent_module):
        from gateway.config import PlatformConfig

        adapter = HelloAgentAdapter(PlatformConfig(enabled=True, token="bad"))

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "bad_token"

    @pytest.mark.asyncio
    async def test_connect_rejects_missing_token(self):
        from gateway.config import PlatformConfig

        adapter = HelloAgentAdapter(PlatformConfig(enabled=True))

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "config_missing"

    @pytest.mark.asyncio
    async def test_connect_rejects_missing_sdk(self, monkeypatch, platform_config):
        monkeypatch.delitem(sys.modules, "helloagent", raising=False)

        adapter = HelloAgentAdapter(platform_config)

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "missing_dep"

    @pytest.mark.asyncio
    async def test_connect_reports_auth_failure(self, monkeypatch, platform_config):
        class AuthFailAgent(FakeAgent):
            def __init__(self, token, relay_url=None, **kwargs):
                super().__init__(token, relay_url, **kwargs)
                self.handle = ""

            async def run(self):
                raise FakeAuthFailedError("rejected")

        install_fake_helloagent(monkeypatch, AuthFailAgent)

        adapter = HelloAgentAdapter(platform_config)

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "auth_failed"

    @pytest.mark.asyncio
    async def test_connect_reports_run_task_failure(self, monkeypatch, platform_config):
        class FailingAgent(FakeAgent):
            def __init__(self, token, relay_url=None, **kwargs):
                super().__init__(token, relay_url, **kwargs)
                self.handle = ""

            async def run(self):
                raise RuntimeError("network down")

        install_fake_helloagent(monkeypatch, FailingAgent)

        adapter = HelloAgentAdapter(platform_config)

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "connect_failed"
        assert "network down" in adapter.fatal_error_message

    @pytest.mark.asyncio
    async def test_connect_times_out_waiting_for_handle(
        self, monkeypatch, platform_config
    ):
        real_sleep = asyncio.sleep

        class NoHandleAgent(FakeAgent):
            def __init__(self, token, relay_url=None, **kwargs):
                super().__init__(token, relay_url, **kwargs)
                self.handle = ""

        class FakeLoop:
            def __init__(self):
                self.current = 0.0

            def time(self):
                self.current += 20.0
                return self.current

        async def yield_once(_delay):
            await real_sleep(0)

        fake_loop = FakeLoop()
        install_fake_helloagent(monkeypatch, NoHandleAgent)
        monkeypatch.setattr(_hello_mod.asyncio, "get_running_loop", lambda: fake_loop)
        monkeypatch.setattr(_hello_mod.asyncio, "sleep", yield_once)

        adapter = HelloAgentAdapter(platform_config)

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "handshake_timeout"


@pytest.mark.asyncio
async def test_standalone_send(fake_helloagent_module, platform_config):
    result = await _standalone_send(platform_config, "alice", "hello")

    assert result == {"success": True, "message_id": "msg_123"}
    assert FakeAgent.instances[0].sent == [
        {"to_handle": "alice", "text": "hello", "conversation_id": None}
    ]
    assert FakeAgent.instances[0].ws.closed is True


@pytest.mark.asyncio
async def test_standalone_send_rejects_media(platform_config):
    result = await _standalone_send(
        platform_config,
        "alice",
        "hello",
        media_files=["image.png"],
    )

    assert result == {"error": "HelloAgent does not support media in v1"}


@pytest.mark.asyncio
async def test_standalone_send_requires_token():
    from gateway.config import PlatformConfig

    result = await _standalone_send(PlatformConfig(enabled=True), "alice", "hello")

    assert result == {"error": "HELLOAGENT_TOKEN not set"}


@pytest.mark.asyncio
async def test_standalone_send_requires_sdk(monkeypatch, platform_config):
    monkeypatch.delitem(sys.modules, "helloagent", raising=False)

    result = await _standalone_send(platform_config, "alice", "hello")

    assert result == {"error": "helloagentai SDK not installed"}


@pytest.mark.asyncio
async def test_standalone_send_reports_auth_failure(monkeypatch, platform_config):
    class AuthFailAgent(FakeAgent):
        def __init__(self, token, relay_url=None, **kwargs):
            super().__init__(token, relay_url, **kwargs)
            self.handle = ""

        async def run(self):
            raise FakeAuthFailedError("rejected")

    install_fake_helloagent(monkeypatch, AuthFailAgent)

    result = await _standalone_send(platform_config, "alice", "hello")

    assert result["error"].startswith("HelloAgent auth failed:")


@pytest.mark.asyncio
async def test_standalone_send_reports_run_failure(monkeypatch, platform_config):
    class FailingAgent(FakeAgent):
        def __init__(self, token, relay_url=None, **kwargs):
            super().__init__(token, relay_url, **kwargs)
            self.handle = ""

        async def run(self):
            raise RuntimeError("network down")

    install_fake_helloagent(monkeypatch, FailingAgent)

    result = await _standalone_send(platform_config, "alice", "hello")

    assert result == {"error": "HelloAgent standalone send failed: network down"}


@pytest.mark.asyncio
async def test_standalone_send_reports_send_failure(monkeypatch, platform_config):
    class SendFailAgent(FakeAgent):
        async def send(self, to_handle, text, conversation_id=None):
            raise RuntimeError("send exploded")

    install_fake_helloagent(monkeypatch, SendFailAgent)

    result = await _standalone_send(platform_config, "alice", "hello")

    assert result == {"error": "HelloAgent standalone send failed: send exploded"}


@pytest.mark.asyncio
async def test_standalone_send_times_out(monkeypatch, platform_config):
    real_sleep = asyncio.sleep

    class NoHandleAgent(FakeAgent):
        def __init__(self, token, relay_url=None, **kwargs):
            super().__init__(token, relay_url, **kwargs)
            self.handle = ""

    async def yield_once(_delay):
        await real_sleep(0)

    install_fake_helloagent(monkeypatch, NoHandleAgent)
    monkeypatch.setattr(_hello_mod.asyncio, "sleep", yield_once)

    result = await _standalone_send(platform_config, "alice", "hello")

    assert result == {"error": "HelloAgent standalone send: handshake timeout"}


def test_register_wires_platform_hooks():
    calls = []

    class Ctx:
        def register_platform(self, **kwargs):
            calls.append(kwargs)

    register(Ctx())

    assert len(calls) == 1
    entry = calls[0]
    assert entry["name"] == "helloagent"
    assert entry["label"] == "HelloAgent"
    assert entry["check_fn"] is check_requirements
    assert entry["validate_config"] is validate_config
    assert entry["cron_deliver_env_var"] == "HELLOAGENT_HOME_CHANNEL"
    assert entry["standalone_sender_fn"] is _standalone_send
    assert entry["allowed_users_env"] == "HELLOAGENT_ALLOWED_USERS"
    assert entry["allow_all_env"] == "HELLOAGENT_ALLOW_ALL_USERS"
    assert entry["pii_safe"] is True
    assert "HelloAgent" in entry["platform_hint"]


def test_is_connected_matches_validate_config(platform_config):
    assert is_connected(platform_config) is True
