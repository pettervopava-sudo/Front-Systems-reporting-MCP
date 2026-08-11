"""Credential loading and validation.

Secrets are kept off __repr__ and __str__ deliberately: the natural failure
mode of an HTTP client is an exception that renders its own config, and a key
pasted into a transcript cannot be un-leaked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

REQUIRED = (
    "FRONT_SYSTEMS_BASE_URL",
    "FRONT_SYSTEMS_SUBSCRIPTION_KEY",
    "FRONT_SYSTEMS_API_KEY",
)


class ConfigError(Exception):
    """Raised when credentials are missing or unusable."""


@dataclass(frozen=True)
class Config:
    base_url: str
    subscription_key: str = field(repr=False)
    api_key: str = field(repr=False)

    def __str__(self) -> str:
        return f"Config(base_url={self.base_url!r}, keys=<redacted>)"


def load_config(env_path: Path | None = None) -> Config:
    path = Path(env_path) if env_path else Path.cwd() / ".env"
    if not path.exists():
        raise ConfigError(
            f"No .env at {path}. It must define: {', '.join(REQUIRED)}."
        )
    raw_values = dotenv_values(path, encoding="utf-8-sig")
    values = {
        k: str(v).strip() if v is not None else "" for k, v in raw_values.items()
    }
    missing = [k for k in REQUIRED if not values.get(k, "").strip()]
    if missing:
        raise ConfigError(
            f"Missing or blank in {path}: {', '.join(missing)}."
        )
    return Config(
        base_url=values["FRONT_SYSTEMS_BASE_URL"].rstrip("/"),
        subscription_key=values["FRONT_SYSTEMS_SUBSCRIPTION_KEY"],
        api_key=values["FRONT_SYSTEMS_API_KEY"],
    )
