import pytest
from pathlib import Path
import front_systems_mcp.config as config_module
from front_systems_mcp.config import load_config, Config, ConfigError


def write_env(tmp_path: Path, **kv: str) -> Path:
    p = tmp_path / ".env"
    p.write_text("\n".join(f"{k}={v}" for k, v in kv.items()))
    return p


def test_loads_all_three_values(tmp_path):
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://example.test/",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="sub123",
        FRONT_SYSTEMS_API_KEY="api456",
    )
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.base_url == "https://example.test"  # trailing slash stripped
    assert cfg.subscription_key == "sub123"
    assert cfg.api_key == "api456"


def test_missing_key_names_the_variable(tmp_path):
    p = write_env(tmp_path, FRONT_SYSTEMS_BASE_URL="https://example.test")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "FRONT_SYSTEMS_SUBSCRIPTION_KEY" in str(exc.value)


def test_blank_value_treated_as_missing(tmp_path):
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://example.test",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="   ",
        FRONT_SYSTEMS_API_KEY="api456",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "FRONT_SYSTEMS_SUBSCRIPTION_KEY" in str(exc.value)


def test_secrets_never_appear_in_repr(tmp_path):
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://example.test",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="supersecretsub",
        FRONT_SYSTEMS_API_KEY="supersecretapi",
    )
    cfg = load_config(p)
    rendered = f"{cfg!r} {cfg!s}"
    assert "supersecretsub" not in rendered
    assert "supersecretapi" not in rendered


def test_double_and_single_quoted_values_are_unquoted(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        'FRONT_SYSTEMS_BASE_URL="https://example.test"\n'
        "FRONT_SYSTEMS_SUBSCRIPTION_KEY='sub123'\n"
        'FRONT_SYSTEMS_API_KEY=api456\n'
    )
    cfg = load_config(p)
    assert cfg.base_url == "https://example.test"
    assert cfg.subscription_key == "sub123"
    assert cfg.api_key == "api456"


def test_utf8_bom_does_not_hide_the_first_variable(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "FRONT_SYSTEMS_BASE_URL=https://example.test\n"
        "FRONT_SYSTEMS_SUBSCRIPTION_KEY=sub123\n"
        "FRONT_SYSTEMS_API_KEY=api456\n",
        encoding="utf-8-sig",
    )
    assert load_config(p).base_url == "https://example.test"


def test_value_containing_equals_is_preserved(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "FRONT_SYSTEMS_BASE_URL=https://example.test\n"
        "FRONT_SYSTEMS_SUBSCRIPTION_KEY=sub123\n"
        "FRONT_SYSTEMS_API_KEY=a=b=c\n"
    )
    assert load_config(p).api_key == "a=b=c"


def test_missing_file_names_the_path_and_the_variables(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "nope.env")
    message = str(exc.value)
    assert "nope.env" in message
    assert "FRONT_SYSTEMS_API_KEY" in message


def test_bare_key_without_equals_is_reported_missing_not_crash(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "FRONT_SYSTEMS_BASE_URL=https://example.test\n"
        "FRONT_SYSTEMS_SUBSCRIPTION_KEY=sub123\n"
        "FRONT_SYSTEMS_API_KEY\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "FRONT_SYSTEMS_API_KEY" in str(exc.value)


def test_explicit_path_still_wins_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONT_SYSTEMS_BASE_URL", "https://from-env.test")
    monkeypatch.setenv("FRONT_SYSTEMS_SUBSCRIPTION_KEY", "envsub")
    monkeypatch.setenv("FRONT_SYSTEMS_API_KEY", "envapi")
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://from-file.test",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="filesub",
        FRONT_SYSTEMS_API_KEY="fileapi",
    )
    assert load_config(p).base_url == "https://from-file.test"


def test_process_environment_is_used_when_no_file_is_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # no .env here
    monkeypatch.delenv("FRONT_SYSTEMS_ENV_FILE", raising=False)
    monkeypatch.setenv("FRONT_SYSTEMS_BASE_URL", "https://from-env.test")
    monkeypatch.setenv("FRONT_SYSTEMS_SUBSCRIPTION_KEY", "envsub")
    monkeypatch.setenv("FRONT_SYSTEMS_API_KEY", "envapi")
    cfg = load_config()
    assert cfg.base_url == "https://from-env.test"
    assert cfg.subscription_key == "envsub"


def test_env_file_variable_is_honoured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("FRONT_SYSTEMS_BASE_URL", "FRONT_SYSTEMS_SUBSCRIPTION_KEY",
                "FRONT_SYSTEMS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    target = tmp_path / "elsewhere.env"
    target.write_text(
        "FRONT_SYSTEMS_BASE_URL=https://pointed.test\n"
        "FRONT_SYSTEMS_SUBSCRIPTION_KEY=psub\n"
        "FRONT_SYSTEMS_API_KEY=papi\n"
    )
    monkeypatch.setenv("FRONT_SYSTEMS_ENV_FILE", str(target))
    assert load_config().base_url == "https://pointed.test"


def test_error_lists_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("FRONT_SYSTEMS_ENV_FILE", "FRONT_SYSTEMS_BASE_URL",
                "FRONT_SYSTEMS_SUBSCRIPTION_KEY", "FRONT_SYSTEMS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    # Isolate from the real project-root .env: this repo's actual .env file
    # (fallback #5) would otherwise satisfy load_config() and mask the
    # "not found" behaviour this test is checking.
    monkeypatch.setattr(
        config_module, "_project_root_env",
        lambda: tmp_path / "no-such-root" / ".env",
    )
    with pytest.raises(ConfigError) as exc:
        load_config()
    message = str(exc.value)
    assert "FRONT_SYSTEMS_API_KEY" in message
    assert str(tmp_path) in message or ".env" in message


def test_no_credential_value_appears_in_the_not_found_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRONT_SYSTEMS_BASE_URL", "https://partial.test")
    monkeypatch.setenv("FRONT_SYSTEMS_SUBSCRIPTION_KEY", "SECRETVALUE123")
    monkeypatch.delenv("FRONT_SYSTEMS_API_KEY", raising=False)
    monkeypatch.delenv("FRONT_SYSTEMS_ENV_FILE", raising=False)
    # Same isolation as above: prevent the real project-root .env from
    # completing the credential set behind this partial process environment.
    monkeypatch.setattr(
        config_module, "_project_root_env",
        lambda: tmp_path / "no-such-root" / ".env",
    )
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "SECRETVALUE123" not in str(exc.value)
