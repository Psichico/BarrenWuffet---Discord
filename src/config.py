"""Loads .env (secrets) + config.yaml (tunables) into a typed Settings object.

Secrets live only in the environment. config.yaml holds the interest profile and
non-secret tunables. Sink webhook URLs are referenced indirectly via
``webhook_env_var`` so the actual URL never touches the config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class LLMConfig:
    # Blank ⇒ "openrouter/auto" (OpenRouter picks the model). Pin a slug in
    # config.yaml (e.g. "anthropic/claude-sonnet-4.5") or use an OpenRouter
    # preset ("@preset/<name>") to choose the model on OpenRouter's side.
    model: str = ""
    max_tokens: int = 1024
    batch_size: int = 20
    rewrite: bool = True
    interest_profile: str = ""
    urgency_guidance: str = ""
    rewrite_style: str = ""


@dataclass
class DedupeConfig:
    prune_older_than_days: int = 30
    path: str = "./seen.db"


@dataclass
class Settings:
    poll_interval_seconds: int
    llm: LLMConfig
    dedupe: DedupeConfig
    poll_interval_weekend_seconds: int = 10800
    sources: dict[str, dict] = field(default_factory=dict)
    sinks: dict[str, dict] = field(default_factory=dict)
    warm_start: bool = True

    # Secrets pulled from env (present here for convenience; components read env
    # directly where the plan requires it).
    openrouter_api_key: str = ""
    finviz_auth_token: str = ""


def load_settings(config_path: str = "config.yaml") -> Settings:
    load_dotenv()

    with open(config_path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    llm_raw = raw.get("llm", {}) or {}
    llm = LLMConfig(
        model=llm_raw.get("model", ""),
        max_tokens=int(llm_raw.get("max_tokens", 1024)),
        batch_size=int(llm_raw.get("batch_size", 20)),
        rewrite=bool(llm_raw.get("rewrite", True)),
        interest_profile=llm_raw.get("interest_profile", ""),
        urgency_guidance=llm_raw.get("urgency_guidance", ""),
        rewrite_style=llm_raw.get("rewrite_style", ""),
    )

    dedupe_raw = raw.get("dedupe", {}) or {}
    dedupe = DedupeConfig(
        prune_older_than_days=int(dedupe_raw.get("prune_older_than_days", 30)),
        # DEDUPE_PATH env wins over config so Render can point at the disk mount.
        path=os.environ.get("DEDUPE_PATH", dedupe_raw.get("path", "./seen.db")),
    )

    return Settings(
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
        llm=llm,
        dedupe=dedupe,
        poll_interval_weekend_seconds=int(raw.get("poll_interval_weekend_seconds", 10800)),
        sources=raw.get("sources", {}) or {},
        sinks=raw.get("sinks", {}) or {},
        warm_start=bool(raw.get("warm_start", True)),
        openrouter_api_key=os.environ.get("OR_NewsFilterAPI", ""),
        finviz_auth_token=os.environ.get("FINVIZ_AUTH_TOKEN", ""),
    )
