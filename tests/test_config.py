import pytest
from pathlib import Path
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
