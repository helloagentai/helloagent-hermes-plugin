from __future__ import annotations

import json

import pytest

import helloagent_hermes.install as install_module
from helloagent_hermes.install import connect, install


def test_install_creates_user_plugin_symlink(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    target = install()

    assert target == tmp_path / "plugins" / "helloagent"
    assert target.is_symlink()
    assert (target / "plugin.yaml").is_file()
    assert (target / "__init__.py").is_file()


def test_install_refuses_existing_target_without_force(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install()

    with pytest.raises(FileExistsError):
        install()


def test_install_copy_mode_replaces_existing_target_with_force(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    target = install()

    copied = install(force=True, copy=True)

    assert copied == target
    assert copied.is_dir()
    assert not copied.is_symlink()
    assert (copied / "adapter.py").is_file()


def test_install_force_replaces_existing_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    target = tmp_path / "plugins" / "helloagent"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("old", encoding="utf-8")

    installed = install(force=True)

    assert installed == target
    assert installed.is_symlink()
    assert not (target / "stale.txt").exists()


def test_upsert_env_value_replaces_existing_and_commented_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OTHER=1\n"
        "# HELLOAGENT_TOKEN=old\n"
        "  HELLOAGENT_ALLOWED_USERS=old\n",
        encoding="utf-8",
    )

    install_module._upsert_env_value(env_path, "HELLOAGENT_TOKEN", "ha_new")
    install_module._upsert_env_value(env_path, "HELLOAGENT_ALLOWED_USERS", "alice")

    assert env_path.read_text(encoding="utf-8") == (
        "OTHER=1\n"
        "HELLOAGENT_TOKEN=ha_new\n"
        "HELLOAGENT_ALLOWED_USERS=alice\n"
    )


def test_upsert_env_value_appends_after_blank_when_needed(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=1\n", encoding="utf-8")

    install_module._upsert_env_value(env_path, "HELLOAGENT_TOKEN", "ha_new")

    assert env_path.read_text(encoding="utf-8") == "OTHER=1\n\nHELLOAGENT_TOKEN=ha_new\n"


def test_connect_writes_credentials_env_and_runs_hermes_enable(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("helloagent_hermes.install._run_hermes", lambda args: calls.append(args))

    result = connect(
        token="ha_test",
        handle="@alice/jarvis",
        allow_from="alice, bob",
        home_channel="alice",
        relay_url="ws://relay.test/v1/ws",
    )

    assert result["plugin_path"] == tmp_path / "plugins" / "helloagent"
    assert calls == [["plugins", "enable", "helloagent"]]

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "HELLOAGENT_TOKEN=ha_test" in env_text
    assert "HELLOAGENT_ALLOWED_USERS=alice,bob" in env_text
    assert "HELLOAGENT_HOME_CHANNEL=alice" in env_text
    assert "HELLOAGENT_RELAY_URL=ws://relay.test/v1/ws" in env_text

    creds_path = tmp_path / "credentials" / "helloagent.json"
    creds = json.loads(creds_path.read_text(encoding="utf-8"))
    assert creds["token"] == "ha_test"
    assert creds["handle"] == "alice/jarvis"
    assert creds["owner_handle"] == "alice"
    assert creds["agent_name"] == "jarvis"
    assert creds_path.stat().st_mode & 0o777 == 0o600


def test_connect_can_allow_all_and_restart_gateway(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("helloagent_hermes.install._run_hermes", lambda args: calls.append(args))

    connect(token="ha_test", allow_all=True, restart_gateway=True)

    assert calls == [
        ["plugins", "enable", "helloagent"],
        ["gateway", "restart"],
    ]
    assert "HELLOAGENT_ALLOW_ALL_USERS=true" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )


def test_connect_can_skip_enable(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "helloagent_hermes.install._run_hermes",
        lambda args: pytest.fail("should not run hermes"),
    )

    connect(token="ha_test", enable=False)


def test_connect_rejects_bad_token(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with pytest.raises(ValueError, match="must start with 'ha_'"):
        connect(token="bad")


def test_find_hermes_prefers_env_bin(monkeypatch):
    monkeypatch.setenv("HERMES_BIN", "/custom/hermes")

    assert install_module._find_hermes() == "/custom/hermes"


def test_find_hermes_uses_path_lookup(monkeypatch):
    monkeypatch.delenv("HERMES_BIN", raising=False)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: "/usr/local/bin/hermes")

    assert install_module._find_hermes() == "/usr/local/bin/hermes"


def test_find_hermes_falls_back_to_sibling_executable(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_BIN", raising=False)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: None)
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    hermes = bin_dir / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(install_module.sys, "executable", str(bin_dir / "python"))

    assert install_module._find_hermes() == str(hermes)


def test_find_hermes_returns_none_when_not_found(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_BIN", raising=False)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(install_module.sys, "executable", str(tmp_path / "python"))

    assert install_module._find_hermes() is None


def test_run_hermes_invokes_resolved_binary(monkeypatch):
    calls = []
    monkeypatch.setattr(install_module, "_find_hermes", lambda: "/bin/hermes")
    monkeypatch.setattr(
        install_module.subprocess,
        "run",
        lambda args, check: calls.append((args, check)),
    )

    install_module._run_hermes(["plugins", "enable", "helloagent"])

    assert calls == [(["/bin/hermes", "plugins", "enable", "helloagent"], True)]


def test_run_hermes_requires_binary(monkeypatch):
    monkeypatch.setattr(install_module, "_find_hermes", lambda: None)

    with pytest.raises(RuntimeError, match="Could not find"):
        install_module._run_hermes(["plugins", "list"])


def test_main_install_command_prints_next_step(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    install_module.main(["install"])

    out = capsys.readouterr().out
    assert f"Installed HelloAgent Hermes plugin at {tmp_path / 'plugins' / 'helloagent'}" in out
    assert "Next: hermes plugins enable helloagent" in out


def test_main_default_no_args_installs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(install_module.sys, "argv", ["helloagent-hermes"])

    install_module.main()

    assert (tmp_path / "plugins" / "helloagent").is_symlink()
    assert "Installed HelloAgent Hermes plugin" in capsys.readouterr().out


def test_main_connect_uses_getpass_and_prints_restart_hint(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(install_module.getpass, "getpass", lambda prompt: "ha_prompted")
    monkeypatch.setattr("helloagent_hermes.install._run_hermes", lambda args: calls.append(args))

    install_module.main(["connect", "--handle", "@alice/jarvis", "--allow-from", "alice", "--no-enable"])

    out = capsys.readouterr().out
    assert "Saved HelloAgent credentials" in out
    assert "Updated Hermes env" in out
    assert "Next: hermes gateway restart" in out
    assert calls == []
    creds = json.loads((tmp_path / "credentials" / "helloagent.json").read_text(encoding="utf-8"))
    assert creds["token"] == "ha_prompted"


def test_main_connect_restart_suppresses_restart_hint(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("helloagent_hermes.install._run_hermes", lambda args: calls.append(args))

    install_module.main(["connect", "--token", "ha_test", "--restart-gateway"])

    out = capsys.readouterr().out
    assert "Next: hermes gateway restart" not in out
    assert calls == [
        ["plugins", "enable", "helloagent"],
        ["gateway", "restart"],
    ]


def test_main_bad_token_exits_with_value_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with pytest.raises(ValueError, match="must start with 'ha_'"):
        install_module.main(["connect", "--token", "bad"])
