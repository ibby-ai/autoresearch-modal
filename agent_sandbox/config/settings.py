"""Settings for the autoresearch Modal wrapper."""

from functools import lru_cache

import modal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings for the autoresearch runtime."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str = ""
    anthropic_secret_name: str = Field(
        default="anthropic-secret",
        description="Modal secret that provides ANTHROPIC_API_KEY for Claude CLI runs.",
    )
    autoresearch_base_branch: str = Field(
        default="master",
        description="Git branch used when seeding a new autoresearch/<run_tag> branch in the workspace.",
    )
    autoresearch_workspace_vol_name: str = Field(
        default="autoresearch-workspace",
        description="Modal Volume name for persistent per-run workspace repos.",
    )
    autoresearch_cache_vol_name: str = Field(
        default="autoresearch-cache",
        description="Modal Volume name for ~/.cache/autoresearch and compiler caches.",
    )
    autoresearch_workspace_root: str = Field(
        default="/home/agent/workspaces/autoresearch",
        description="Mount path for the persistent per-run workspace volume.",
    )
    autoresearch_cache_root: str = Field(
        default="/home/agent/.cache/autoresearch",
        description="Mount path for the upstream cache volume.",
    )
    autoresearch_prepare_num_shards: int = Field(
        default=10,
        description="Shard count used by prepare.py when bootstrapping the cache.",
    )
    autoresearch_gpu: str = Field(
        default="H100",
        description="GPU type requested for direct baseline smokes and Claude-driven agent runs.",
    )
    autoresearch_prepare_timeout: int = Field(
        default=60 * 60,
        description="Timeout in seconds for prepare_autoresearch_run.",
    )
    autoresearch_train_timeout: int = Field(
        default=60 * 20,
        description="Timeout in seconds for one direct baseline run.",
    )
    autoresearch_claude_timeout: int = Field(
        default=60 * 60 * 8,
        description="Timeout in seconds for one Claude-driven autoresearch session.",
    )
    autoresearch_git_user_name: str = Field(
        default="Autoresearch Modal",
        description="Git user.name configured inside the per-run workspace repo.",
    )
    autoresearch_git_user_email: str = Field(
        default="autoresearch@modal.local",
        description="Git user.email configured inside the per-run workspace repo.",
    )


def get_modal_secrets() -> list[modal.Secret]:
    """Return the Modal secrets needed for Claude-driven runs."""
    settings = get_settings()
    return [
        modal.Secret.from_name(
            settings.anthropic_secret_name,
            required_keys=["ANTHROPIC_API_KEY"],
        )
    ]


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
