"""Credential loading and validation.

Secrets are kept off __repr__ and __str__ deliberately: the natural failure
mode of an HTTP client is an exception that renders its own config, and a key
pasted into a transcript cannot be un-leaked.

An MCP server is launched BY THE CLIENT, which does not guarantee a working
directory, so `Path.cwd() / ".env"` alone is not a reliable place to look for
credentials. `load_config` therefore tries, in order, an explicit path, the
`FRONT_SYSTEMS_ENV_FILE` environment variable, the process environment
itself, `Path.cwd() / ".env"`, and finally a `.env` beside the installed
package (so `claude mcp add` works regardless of launch directory).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

REQUIRED = (
    "FRONT_SYSTEMS_BASE_URL",
    "FRONT_SYSTEMS_SUBSCRIPTION_KEY",
    "FRONT_SYSTEMS_API_KEY",
)

ENV_FILE_VAR = "FRONT_SYSTEMS_ENV_FILE"


class ConfigError(Exception):
    """Raised when credentials are missing or unusable."""


@dataclass(frozen=True)
class Config:
    base_url: str
    subscription_key: str = field(repr=False)
    api_key: str = field(repr=False)

    def __str__(self) -> str:
        return f"Config(base_url={self.base_url!r}, keys=<redacted>)"


def _values_from_env_file(path: Path) -> dict[str, str] | None:
    """Read and normalise REQUIRED values from a .env file, if it exists."""
    if not path.exists():
        return None
    raw_values = dotenv_values(path, encoding="utf-8-sig")
    return {
        k: str(v).strip() if v is not None else "" for k, v in raw_values.items()
    }


def _values_from_process_env() -> dict[str, str]:
    return {k: os.environ.get(k, "").strip() for k in REQUIRED}


def _complete(values: dict[str, str] | None) -> bool:
    if values is None:
        return False
    return all(values.get(k, "").strip() for k in REQUIRED)


def _project_root_env() -> Path:
    # src/front_systems_mcp/config.py -> parents[2] is the project root.
    return Path(__file__).resolve().parents[2] / ".env"


def load_config(env_path: Path | None = None) -> Config:
    if env_path is not None:
        path = Path(env_path)
        if not path.exists():
            raise ConfigError(
                f"No .env at {path}. It must define: {', '.join(REQUIRED)}."
            )
        values = _values_from_env_file(path)
        missing = [k for k in REQUIRED if not (values or {}).get(k, "").strip()]
        if missing:
            raise ConfigError(
                f"Missing or blank in {path}: {', '.join(missing)}."
            )
        return _build_config(values)  # type: ignore[arg-type]

    searched: list[str] = []

    env_file_var_value = os.environ.get(ENV_FILE_VAR, "").strip()
    if env_file_var_value:
        candidate = Path(env_file_var_value)
        values = _values_from_env_file(candidate)
        if _complete(values):
            return _build_config(values)  # type: ignore[arg-type]
        searched.append(f"{ENV_FILE_VAR}={candidate}")
    else:
        searched.append(f"{ENV_FILE_VAR} (not set)")

    process_values = _values_from_process_env()
    if _complete(process_values):
        return _build_config(process_values)
    searched.append("process environment")

    cwd_candidate = Path.cwd() / ".env"
    cwd_values = _values_from_env_file(cwd_candidate)
    if _complete(cwd_values):
        return _build_config(cwd_values)  # type: ignore[arg-type]
    searched.append(str(cwd_candidate))

    root_candidate = _project_root_env()
    root_values = _values_from_env_file(root_candidate)
    if _complete(root_values):
        return _build_config(root_values)  # type: ignore[arg-type]
    searched.append(str(root_candidate))

    raise ConfigError(
        "No complete credentials found. Searched: "
        + "; ".join(searched)
        + f". Set these via a .env file or environment variables: {', '.join(REQUIRED)}."
    )


def _build_config(values: dict[str, str]) -> Config:
    return Config(
        base_url=values["FRONT_SYSTEMS_BASE_URL"].rstrip("/"),
        subscription_key=values["FRONT_SYSTEMS_SUBSCRIPTION_KEY"],
        api_key=values["FRONT_SYSTEMS_API_KEY"],
    )
