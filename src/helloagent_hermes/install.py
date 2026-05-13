"""Install helper for current Hermes user-plugin discovery.

Hermes can load PyPI entry-point plugins at runtime, but Hermes 0.13's
``hermes plugins enable`` / ``list`` commands only inspect bundled and
``~/.hermes/plugins`` directory plugins. This helper links the installed
package directory into ``~/.hermes/plugins/helloagent`` so the existing CLI
can list and enable the platform normally.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def _env_path() -> Path:
    return _hermes_home() / ".env"


def _credentials_path() -> Path:
    return _hermes_home() / "credentials" / "helloagent.json"


def _upsert_env_value(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"{key}={value}"
    updated = False
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}=") or stripped.startswith(f"#{key}="):
            lines[idx] = replacement
            updated = True
            break
    if not updated:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_credentials(token: str, handle: str = "") -> Path:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    normalized_handle = handle.strip().lstrip("@")
    owner_handle = ""
    agent_name = ""
    if "/" in normalized_handle:
        owner_handle, agent_name = normalized_handle.split("/", 1)

    linked_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "handle": normalized_handle,
                "agent_name": agent_name,
                "owner_handle": owner_handle,
                "token": token,
                "linked_at": linked_at,
            },
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
    return path


def _find_hermes() -> str | None:
    env_bin = os.environ.get("HERMES_BIN", "").strip()
    if env_bin:
        return env_bin
    found = shutil.which("hermes")
    if found:
        return found
    sibling = Path(sys.executable).with_name("hermes")
    return str(sibling) if sibling.exists() else None


def _run_hermes(args: list[str]) -> None:
    hermes_bin = _find_hermes()
    if not hermes_bin:
        raise RuntimeError("Could not find the 'hermes' command. Set HERMES_BIN.")
    subprocess.run([hermes_bin, *args], check=True)


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_text(label: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _prompt_yes_no(label: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    value = input(f"{label}{suffix}: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


def install(*, force: bool = False, copy: bool = False) -> Path:
    source = Path(__file__).resolve().parent
    target = _hermes_home() / "plugins" / "helloagent"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() or target.is_symlink():
        if not force:
            raise FileExistsError(
                f"{target} already exists; rerun with --force to replace it"
            )
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    if copy:
        shutil.copytree(source, target)
    else:
        target.symlink_to(source, target_is_directory=True)
    return target


def connect(
    *,
    token: str,
    handle: str = "",
    allow_from: str = "",
    allow_all: bool = False,
    force_install: bool = True,
    copy: bool = False,
    enable: bool = True,
    restart_gateway: bool = False,
) -> dict[str, Path | None]:
    token = token.strip()
    if not token.startswith("ha_"):
        raise ValueError("HelloAgent token must start with 'ha_'")

    plugin_path = install(force=force_install, copy=copy)
    credentials_path = _write_credentials(token, handle=handle)

    env_path = _env_path()
    _upsert_env_value(env_path, "HELLOAGENT_TOKEN", token)
    if allow_from:
        _upsert_env_value(env_path, "HELLOAGENT_ALLOWED_USERS", allow_from.replace(" ", ""))
    if allow_all:
        _upsert_env_value(env_path, "HELLOAGENT_ALLOW_ALL_USERS", "true")

    if enable:
        _run_hermes(["plugins", "enable", "helloagent"])
    if restart_gateway:
        _run_hermes(["gateway", "restart"])

    return {
        "plugin_path": plugin_path,
        "credentials_path": credentials_path,
        "env_path": env_path,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="helloagent-hermes",
        description="Install and connect the HelloAgent Hermes plugin.",
    )
    sub = parser.add_subparsers(dest="command")

    install_parser = sub.add_parser(
        "install",
        help="Install the plugin directory link into ~/.hermes/plugins.",
    )
    install_parser.add_argument("--force", action="store_true", help="Replace existing link.")
    install_parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of creating a symlink.",
    )

    connect_parser = sub.add_parser(
        "connect",
        help="Install, enable, and configure HelloAgent for this Hermes gateway.",
    )
    connect_parser.add_argument("--token", help="HelloAgent ha_* agent token.")
    connect_parser.add_argument("--handle", default="", help="Optional @owner/agent handle metadata.")
    connect_parser.add_argument(
        "--allow-from",
        default="",
        help="Comma-separated HelloAgent user handles allowed to DM this agent.",
    )
    connect_parser.add_argument(
        "--allow-all",
        action="store_true",
        help="Allow any HelloAgent user. Useful for smoke tests; prefer --allow-from.",
    )
    connect_parser.add_argument("--copy", action="store_true", help="Copy files instead of symlink.")
    connect_parser.add_argument(
        "--no-enable",
        action="store_true",
        help="Write config but do not run 'hermes plugins enable helloagent'.",
    )
    connect_parser.add_argument(
        "--restart-gateway",
        action="store_true",
        help="Restart Hermes gateway after configuring.",
    )

    if argv is None and len(sys.argv) == 1:
        argv = ["install"]
    args = parser.parse_args(argv)

    if args.command in {None, "install"}:
        target = install(force=getattr(args, "force", False), copy=getattr(args, "copy", False))
        print(f"Installed HelloAgent Hermes plugin at {target}")
        print("Next: hermes plugins enable helloagent")
        return

    interactive = _is_interactive()
    token = args.token or getpass.getpass("HelloAgent ha_* token: ")
    allow_from = args.allow_from
    restart_gateway = args.restart_gateway

    if interactive:
        if not args.allow_all and not allow_from:
            allow_from = _prompt_text(
                "Allowed HelloAgent handles (comma-separated, optional)"
            )
        if not restart_gateway:
            restart_gateway = _prompt_yes_no("Restart Hermes gateway now", default=True)

    paths = connect(
        token=token,
        handle=args.handle,
        allow_from=allow_from,
        allow_all=args.allow_all,
        copy=args.copy,
        enable=not args.no_enable,
        restart_gateway=restart_gateway,
    )
    print(f"Installed HelloAgent Hermes plugin at {paths['plugin_path']}")
    print(f"Saved HelloAgent credentials at {paths['credentials_path']}")
    print(f"Updated Hermes env at {paths['env_path']}")
    if not restart_gateway:
        print("Next: hermes gateway restart")


if __name__ == "__main__":
    main()
