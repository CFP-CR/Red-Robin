"""Typed application settings via pydantic-settings.

Settings are read from the process environment (and, when invoked from the CLI,
from a .env file that the CLI loads at command entry time — never at import).

Root path REDROBIN_ROOT defaults to ~/.redrobin and drives derived paths;
each derived path can be overridden individually if needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _default_root() -> Path:
    return Path.home() / ".redrobin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",  # we use explicit names like REDROBIN_PORT
        case_sensitive=False,
        extra="ignore",
        env_file=None,  # CLI loads dotenv manually; tests use monkeypatch.setenv
    )

    # Secrets (no default — required at boot if Layer B is wanted)
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    voyage_api_key: str | None = Field(default=None, alias="VOYAGE_API_KEY")

    # Daemon network
    host: str = Field(default="127.0.0.1", alias="REDROBIN_HOST")
    port: int = Field(default=7777, alias="REDROBIN_PORT")

    # Root and derived paths
    root: Path = Field(default_factory=_default_root, alias="REDROBIN_ROOT")
    rules_dir: Path | None = Field(default=None, alias="REDROBIN_RULES_DIR")
    working_memory_path: Path | None = Field(default=None, alias="REDROBIN_WORKING_MEMORY_PATH")
    episodic_path: Path | None = Field(default=None, alias="REDROBIN_EPISODIC_PATH")
    longterm_path: Path | None = Field(default=None, alias="REDROBIN_LONGTERM_PATH")
    summary_path: Path | None = Field(default=None, alias="REDROBIN_SUMMARY_PATH")
    engagements_dir: Path | None = Field(default=None, alias="REDROBIN_ENGAGEMENTS_DIR")
    adapters_dir: Path | None = Field(default=None, alias="REDROBIN_ADAPTERS_DIR")
    pid_file: Path | None = Field(default=None, alias="REDROBIN_PID_FILE")
    transport_token_file: Path | None = Field(default=None, alias="REDROBIN_TRANSPORT_TOKEN_FILE")

    # Engagement
    engagement_slug: str | None = Field(default=None, alias="REDROBIN_ENGAGEMENT_SLUG")
    engagement_id: str = Field(default="eng-default", alias="REDROBIN_ENGAGEMENT_ID")

    # Memory / promotion / summarizer tuning
    promoter_interval_sec: float = Field(default=60.0, alias="REDROBIN_PROMOTER_INTERVAL_SEC")
    promoter_min_occurrences: int = Field(default=3, alias="REDROBIN_PROMOTER_MIN_OCCURRENCES")
    l5_window: int = Field(default=25, alias="REDROBIN_L5_WINDOW")
    l5_interval_sec: float = Field(default=30.0, alias="REDROBIN_L5_INTERVAL_SEC")

    # Working-memory enrichment (Plan B)
    enrichment_enabled: bool = Field(default=True, alias="REDROBIN_ENRICHMENT_ENABLED")
    enrichment_model: str = Field(default="claude-haiku-4-5", alias="REDROBIN_ENRICHMENT_MODEL")
    enrichment_min_output_chars: int = Field(
        default=8, alias="REDROBIN_ENRICHMENT_MIN_OUTPUT_CHARS"
    )
    enrichment_chunk_max_chars: int = Field(
        default=16000, alias="REDROBIN_ENRICHMENT_CHUNK_MAX_CHARS"
    )
    enrichment_denylist: Annotated[list[str], NoDecode] = Field(
        # Empty by default: enrich-by-default surfaces loot from dir/ls/ps output
        # (the min_output_chars floor already skips no-output commands like cd/sleep).
        # Operator-tunable via REDROBIN_ENRICHMENT_DENYLIST (CSV or JSON) to mute a
        # command that proves noisy in a live op.
        default_factory=list,
        alias="REDROBIN_ENRICHMENT_DENYLIST",
    )
    enrichment_max_concurrency: int = Field(default=4, alias="REDROBIN_ENRICHMENT_MAX_CONCURRENCY")
    enrichment_self_annotation_prefix: str = Field(
        default="[Red Robin]", alias="REDROBIN_ENRICHMENT_SELF_ANNOTATION_PREFIX"
    )

    # AI assistant (reactive grounded recommendation — Task 2)
    # Off by default: the feature is built incrementally (reactive mode is 1 of 3
    # trigger modes; proactive/conversational land in later tasks). Dark-launched
    # so a merge to the shared integration branch stays inert until an operator
    # opts in with REDROBIN_ASSISTANT_ENABLED=1. Flip the default to True once the
    # whole assistant ships and its whole-feature smoke/review sign off.
    assistant_enabled: bool = Field(default=False, alias="REDROBIN_ASSISTANT_ENABLED")
    assistant_model: str = Field(default="claude-haiku-4-5", alias="REDROBIN_ASSISTANT_MODEL")
    assistant_cooldown_sec: float = Field(default=30.0, alias="REDROBIN_ASSISTANT_COOLDOWN_SEC")
    assistant_max_tokens: int = Field(default=2048, alias="REDROBIN_ASSISTANT_MAX_TOKENS")
    # Context-rot cap (spec §6.B refinement 2): most-recent items kept per working-
    # memory category in the pushed context so long engagements don't re-send an
    # ever-growing resident set every cycle.
    assistant_wm_max_items: int = Field(default=20, alias="REDROBIN_ASSISTANT_WM_MAX_ITEMS")
    # Proactive mode (Task 4 §6.A): X = lull before a self-triggered check;
    # N = min interval between proactive utterances (rate-limit). Tunable, not hardcoded.
    assistant_lull_sec: float = Field(default=300.0, alias="REDROBIN_ASSISTANT_LULL_SEC")
    assistant_proactive_min_interval_sec: float = Field(
        default=600.0, alias="REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC"
    )

    # Operator policy
    allow_degraded_layer_b: bool = Field(default=False, alias="REDROBIN_ALLOW_DEGRADED")

    @model_validator(mode="after")
    def _derive_paths(self) -> "Settings":
        if self.rules_dir is None:
            self.rules_dir = self.root / "rules"
        if self.working_memory_path is None:
            self.working_memory_path = self.root / "working_memory.json"
        if self.episodic_path is None:
            self.episodic_path = self.root / "episodic.db"
        if self.longterm_path is None:
            self.longterm_path = self.root / "longterm.db"
        if self.summary_path is None:
            self.summary_path = self.root / "context_summaries.db"
        if self.engagements_dir is None:
            self.engagements_dir = self.root / "engagements"
        if self.adapters_dir is None:
            self.adapters_dir = self.root / "adapters"
        if self.pid_file is None:
            self.pid_file = self.root / "daemon.pid"
        if self.transport_token_file is None:
            self.transport_token_file = self.root / "transport.token"
        return self

    @field_validator("port")
    @classmethod
    def _port_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"REDROBIN_PORT out of range: {v}")
        return v

    @field_validator(
        "promoter_min_occurrences",
        "l5_window",
        "enrichment_min_output_chars",
        "enrichment_chunk_max_chars",
        "enrichment_max_concurrency",
        "assistant_max_tokens",
        "assistant_wm_max_items",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be >= 1, got {v}")
        return v

    @field_validator(
        "promoter_interval_sec",
        "l5_interval_sec",
        "assistant_cooldown_sec",
        "assistant_lull_sec",
        "assistant_proactive_min_interval_sec",
    )
    @classmethod
    def _positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v

    @field_validator("enrichment_denylist", mode="before")
    @classmethod
    def _parse_denylist(cls, v: object) -> object:
        """Accept the env override as a JSON array OR a comma-separated string.

        ``NoDecode`` on the field stops pydantic-settings from JSON-decoding the
        env value first, so a natural ``REDROBIN_ENRICHMENT_DENYLIST=ls,cd`` works
        as well as ``'["ls","cd"]'`` — matching how scope lists are CSV-parsed
        elsewhere (cli_helpers/engagement_cmd). The default_factory list is not a
        string and passes through unchanged.
        """
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v
