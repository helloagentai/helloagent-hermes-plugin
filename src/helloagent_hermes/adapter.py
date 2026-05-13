"""HelloAgent platform adapter for Hermes Agent."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

DEFAULT_RELAY_URL = "wss://api.helloagent.cc/v1/ws"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_allowlist(env_value: Optional[str], config_value: Any) -> set[str]:
    values: list[str] = []
    if env_value is not None:
        values = [part.strip() for part in env_value.split(",")]
    elif isinstance(config_value, str):
        values = [part.strip() for part in config_value.split(",")]
    elif isinstance(config_value, (list, tuple, set)):
        values = [str(part).strip() for part in config_value]
    return {value.lower() for value in values if value}


def _credentials_path() -> Path:
    return get_hermes_home() / "credentials" / "helloagent.json"


def _read_credentials_file() -> dict:
    path = _credentials_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("HelloAgent: failed to read %s", path, exc_info=True)
        return {}


def _write_credentials_file(token: str, handle: str = "") -> None:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    owner_handle = ""
    agent_name = ""
    normalized_handle = handle.strip().lstrip("@")
    if "/" in normalized_handle:
        owner_handle, agent_name = normalized_handle.split("/", 1)

    linked_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    data = {
        "version": 1,
        "handle": normalized_handle,
        "agent_name": agent_name,
        "owner_handle": owner_handle,
        "token": token,
        "linked_at": linked_at,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _resolve_token(config) -> str:
    extra = getattr(config, "extra", {}) or {}
    creds = _read_credentials_file()
    return (
        os.getenv("HELLOAGENT_TOKEN")
        or getattr(config, "token", "")
        or extra.get("token", "")
        or creds.get("token", "")
        or ""
    ).strip()


class HelloAgentAdapter(BasePlatformAdapter):
    """Relay HelloAgent DMs into the Hermes gateway pipeline."""

    SUPPORTS_MESSAGE_EDITING = False

    def __init__(self, config, **kwargs):
        super().__init__(config=config, platform=Platform("helloagent"))
        extra = getattr(config, "extra", {}) or {}

        self._token = _resolve_token(config)
        self._relay_url = DEFAULT_RELAY_URL
        self._display_name = (
            os.getenv("HELLOAGENT_DISPLAY_NAME")
            or extra.get("display_name", "")
            or ""
        )
        self._allowed_users = _parse_allowlist(
            os.getenv("HELLOAGENT_ALLOWED_USERS"),
            extra.get("allowed_users", []),
        )
        self._allow_all = _env_bool("HELLOAGENT_ALLOW_ALL_USERS", False)

        self._sdk_agent = None
        self._sdk_run_task: Optional[asyncio.Task] = None
        self._handle: Optional[str] = None
        self._conversation_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "HelloAgent"

    async def connect(self) -> bool:
        if not self._token:
            self._set_fatal_error(
                "config_missing",
                "HELLOAGENT_TOKEN must be set",
                retryable=False,
            )
            return False
        if not self._token.startswith("ha_"):
            self._set_fatal_error(
                "bad_token",
                "HELLOAGENT_TOKEN must start with 'ha_'",
                retryable=False,
            )
            return False

        try:
            from helloagent import Agent, AuthFailedError
        except ImportError:
            self._set_fatal_error(
                "missing_dep",
                "helloagentai SDK not installed; run 'pip install helloagent-hermes-plugin'",
                retryable=False,
            )
            return False

        self._sdk_agent = Agent(token=self._token, relay_url=self._relay_url)

        @self._sdk_agent.on_message
        async def _on_msg(msg):
            await self._handle_inbound(msg)
            return None

        self._sdk_run_task = asyncio.create_task(self._sdk_agent.run())

        deadline = asyncio.get_running_loop().time() + 15.0
        while asyncio.get_running_loop().time() < deadline:
            if getattr(self._sdk_agent, "handle", ""):
                self._handle = self._sdk_agent.handle
                break
            if self._sdk_run_task.done():
                exc = self._sdk_run_task.exception()
                if isinstance(exc, AuthFailedError):
                    self._set_fatal_error("auth_failed", str(exc), retryable=False)
                else:
                    self._set_fatal_error(
                        "connect_failed",
                        str(exc) if exc else "HelloAgent connection stopped",
                        retryable=True,
                    )
                await self.disconnect()
                return False
            await asyncio.sleep(0.05)
        else:
            self._set_fatal_error(
                "handshake_timeout",
                "HelloAgent handshake did not complete within 15s",
                retryable=True,
            )
            await self.disconnect()
            return False

        self._mark_connected()
        logger.info("HelloAgent: connected as @%s via %s", self._handle, self._relay_url)
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        ws = getattr(self._sdk_agent, "ws", None)
        if ws is not None:
            with contextlib.suppress(Exception):
                close_result = ws.close()
                if inspect.isawaitable(close_result):
                    await close_result
        if self._sdk_run_task is not None and not self._sdk_run_task.done():
            self._sdk_run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sdk_run_task
        self._sdk_run_task = None
        self._sdk_agent = None
        self._handle = None

    async def _handle_inbound(self, msg) -> None:
        """Translate an SDK IncomingMessage into Hermes' MessageEvent shape."""
        from_handle = str(getattr(msg, "from_handle", "") or "")
        if not from_handle:
            logger.debug("HelloAgent: inbound dropped; message has no from_handle")
            return
        if not self._passes_adapter_prefilter(from_handle):
            logger.debug("HelloAgent: dropping message from unauthorized @%s", from_handle)
            return

        conversation_id = str(getattr(msg, "conversation_id", "") or "")
        if conversation_id:
            self._conversation_ids[from_handle] = conversation_id

        if not self._message_handler:
            logger.warning("HelloAgent: inbound dropped; gateway not ready")
            return

        message_id = str(getattr(msg, "message_id", "") or "")
        source = self.build_source(
            chat_id=from_handle,
            chat_name=from_handle,
            chat_type="dm",
            user_id=from_handle,
            user_name=from_handle,
            message_id=message_id,
        )
        event = MessageEvent(
            text=str(getattr(msg, "text", "") or ""),
            message_type=MessageType.TEXT,
            source=source,
            raw_message=msg,
            message_id=message_id,
            timestamp=_dt.datetime.now(),
        )
        await self.handle_message(event)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if self._sdk_agent is None or not self._handle:
            return SendResult(success=False, error="HelloAgent: not connected")
        try:
            message_id = await self._sdk_agent.send(
                to_handle=chat_id,
                text=content,
                conversation_id=self._conversation_ids.get(chat_id),
            )
            return SendResult(success=True, message_id=str(message_id))
        except Exception as e:
            logger.warning("HelloAgent send failed: %s", e)
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "chat_id": chat_id,
            "name": chat_id,
            "type": "dm",
        }

    def _passes_adapter_prefilter(self, handle: str) -> bool:
        if self._allow_all:
            return True
        if not self._allowed_users:
            return True
        return handle.lower() in self._allowed_users


def check_requirements() -> bool:
    try:
        import helloagent  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config) -> bool:
    return bool(_resolve_token(config))


def is_connected(config) -> bool:
    return validate_config(config)


def interactive_setup() -> None:
    from hermes_cli.setup import (
        get_env_value,
        print_header,
        print_info,
        print_success,
        print_warning,
        prompt,
        prompt_yes_no,
        save_env_value,
    )

    print_header("HelloAgent")
    existing = get_env_value("HELLOAGENT_TOKEN")
    if existing:
        print_info("HelloAgent is already configured.")
        if not prompt_yes_no("Reconfigure HelloAgent?", False):
            return

    print_info("Open https://app.helloagent.cc/app/agents/new in a browser.")
    print_info("Create an agent and copy its token (starts with 'ha_').")
    token = prompt("HelloAgent ha_* token", password=True).strip()
    if not token.startswith("ha_"):
        print_warning("Token must start with 'ha_'; skipping HelloAgent setup.")
        return

    _write_credentials_file(token)
    save_env_value("HELLOAGENT_TOKEN", token)

    allowed = prompt(
        "Allowed HelloAgent handles (comma-separated, leave empty to use gateway defaults)",
        default=get_env_value("HELLOAGENT_ALLOWED_USERS") or "",
    ).strip()
    if allowed:
        save_env_value("HELLOAGENT_ALLOWED_USERS", allowed.replace(" ", ""))

    print_success("HelloAgent configuration saved to ~/.hermes/.env")
    print_info("Restart the gateway for changes to take effect: hermes gateway restart")


def _env_enablement() -> dict | None:
    token = (
        os.getenv("HELLOAGENT_TOKEN", "").strip()
        or str(_read_credentials_file().get("token", "")).strip()
    )
    if not token:
        return None

    seed: dict[str, Any] = {}
    display_name = os.getenv("HELLOAGENT_DISPLAY_NAME", "").strip()
    if display_name:
        seed["display_name"] = display_name
    allowed = os.getenv("HELLOAGENT_ALLOWED_USERS")
    if allowed is not None:
        seed["allowed_users"] = [
            item.strip() for item in allowed.split(",") if item.strip()
        ]
    return seed


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    if media_files or force_document:
        return {"error": "HelloAgent does not support media in v1"}

    token = _resolve_token(pconfig)
    if not token:
        return {"error": "HELLOAGENT_TOKEN not set"}

    try:
        from helloagent import Agent, AuthFailedError
    except ImportError:
        return {"error": "helloagentai SDK not installed"}

    agent = Agent(token=token, relay_url=DEFAULT_RELAY_URL)
    run_task = asyncio.create_task(agent.run())
    try:
        for _ in range(200):
            if getattr(agent, "handle", ""):
                break
            if run_task.done():
                exc = run_task.exception()
                if isinstance(exc, AuthFailedError):
                    return {"error": f"HelloAgent auth failed: {exc}"}
                return {"error": f"HelloAgent standalone send failed: {exc}"}
            await asyncio.sleep(0.05)
        else:
            return {"error": "HelloAgent standalone send: handshake timeout"}

        msg_id = await agent.send(to_handle=chat_id, text=message)
        await asyncio.sleep(0.1)
        return {"success": True, "message_id": str(msg_id)}
    except Exception as e:
        return {"error": f"HelloAgent standalone send failed: {e}"}
    finally:
        ws = getattr(agent, "ws", None)
        if ws is not None:
            with contextlib.suppress(Exception):
                close_result = ws.close()
                if inspect.isawaitable(close_result):
                    await close_result
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task


def register(ctx) -> None:
    ctx.register_platform(
        name="helloagent",
        label="HelloAgent",
        adapter_factory=lambda cfg: HelloAgentAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["HELLOAGENT_TOKEN"],
        install_hint="pip install helloagent-hermes-plugin",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        standalone_sender_fn=_standalone_send,
        allowed_users_env="HELLOAGENT_ALLOWED_USERS",
        allow_all_env="HELLOAGENT_ALLOW_ALL_USERS",
        max_message_length=0,
        emoji="👋",
        pii_safe=True,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via HelloAgent. The user reached you through "
            "the HelloAgent mobile or web app. HelloAgent supports plain text "
            "and limited markdown; avoid wide tables, images, or HTML. "
            "Keep responses conversational."
        ),
    )
