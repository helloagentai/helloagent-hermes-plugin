from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

for path in (ROOT / "src", WORKSPACE / "hermes-agent"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def _install_gateway_stubs() -> None:
    """Provide the tiny Hermes surface the adapter tests need in standalone CI."""

    try:
        import gateway.config  # noqa: F401
        import gateway.platforms.base  # noqa: F401
        import gateway.platform_registry  # noqa: F401
        import hermes_constants  # noqa: F401
        return
    except ImportError:
        pass

    gateway = types.ModuleType("gateway")
    config = types.ModuleType("gateway.config")
    platforms = types.ModuleType("gateway.platforms")
    base = types.ModuleType("gateway.platforms.base")
    registry = types.ModuleType("gateway.platform_registry")
    hermes_constants = types.ModuleType("hermes_constants")

    class Platform:
        def __init__(self, value: str):
            self.value = value

        def __str__(self) -> str:
            return self.value

        def __eq__(self, other) -> bool:
            if isinstance(other, Platform):
                return self.value == other.value
            return self.value == other

    @dataclass
    class PlatformConfig:
        enabled: bool = False
        token: str = ""
        extra: dict = field(default_factory=dict)

    class MessageType(Enum):
        TEXT = "text"

    @dataclass
    class MessageEvent:
        text: str
        message_type: MessageType
        source: object
        raw_message: object
        message_id: str
        timestamp: object

    @dataclass
    class SendResult:
        success: bool
        message_id: str | None = None
        error: str | None = None

    class BasePlatformAdapter:
        def __init__(self, config, platform):
            self.config = config
            self.platform = platform
            self._message_handler = None
            self.connected = False
            self.fatal_error_code = None
            self.fatal_error_message = None
            self.fatal_error_retryable = None

        def _set_fatal_error(self, code, message, *, retryable):
            self.fatal_error_code = code
            self.fatal_error_message = message
            self.fatal_error_retryable = retryable

        def _mark_connected(self):
            self.connected = True

        def _mark_disconnected(self):
            self.connected = False

        def build_source(self, **kwargs):
            return SimpleNamespace(platform=self.platform, **kwargs)

        async def handle_message(self, event):
            if self._message_handler is not None:
                return await self._message_handler(event)
            return None

    @dataclass
    class PlatformEntry:
        name: str
        label: str
        adapter_factory: object
        check_fn: object
        validate_config: object
        source: str = "plugin"
        plugin_name: str = "helloagent"
        kwargs: dict = field(default_factory=dict)

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class PlatformRegistry:
        def __init__(self):
            self.entries = {}

        def register(self, entry):
            self.entries[entry.name] = entry

    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()

    config.Platform = Platform
    config.PlatformConfig = PlatformConfig
    base.BasePlatformAdapter = BasePlatformAdapter
    base.MessageEvent = MessageEvent
    base.MessageType = MessageType
    base.SendResult = SendResult
    registry.PlatformEntry = PlatformEntry
    registry.platform_registry = PlatformRegistry()
    hermes_constants.get_hermes_home = get_hermes_home

    sys.modules.setdefault("gateway", gateway)
    sys.modules.setdefault("gateway.config", config)
    sys.modules.setdefault("gateway.platforms", platforms)
    sys.modules.setdefault("gateway.platforms.base", base)
    sys.modules.setdefault("gateway.platform_registry", registry)
    sys.modules.setdefault("hermes_constants", hermes_constants)


_install_gateway_stubs()
