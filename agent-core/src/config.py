from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    agent_core_port: int = Field(default=8000, alias="AGENT_CORE_PORT")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model_routine: str = Field(default="claude-sonnet-4-6", alias="CLAUDE_MODEL_ROUTINE")
    claude_model_complex: str = Field(default="claude-opus-4-6", alias="CLAUDE_MODEL_COMPLEX")

    pg_host: str = Field(default="postgres", alias="PG_HOST")
    pg_port: int = Field(default=5432, alias="PG_PORT")
    pg_db: str = Field(default="dsagents", alias="PG_DB")
    pg_user: str = Field(default="dsagents", alias="PG_USER")
    pg_password: str = Field(default="", alias="PG_PASSWORD")

    minio_endpoint: str = Field(default="minio:9000", alias="MINIO_ENDPOINT")
    minio_root_user: str = Field(default="minioadmin", alias="MINIO_ROOT_USER")
    minio_root_password: str = Field(default="", alias="MINIO_ROOT_PASSWORD")
    minio_bucket: str = Field(default="dsagents", alias="MINIO_BUCKET")

    sandbox_timeout_seconds: int = Field(default=300, alias="SANDBOX_TIMEOUT_SECONDS")
    sandbox_memory_limit: str = Field(default="2g", alias="SANDBOX_MEMORY_LIMIT")
    sandbox_cpu_limit: float = Field(default=2.0, alias="SANDBOX_CPU_LIMIT")
    sandbox_image: str = Field(default="ds-agents-sandbox:latest", alias="SANDBOX_IMAGE")
    sandbox_shared_volume: str = Field(default="cortex_sandbox_tmp", alias="SANDBOX_SHARED_VOLUME")
    sandbox_shared_mount: str = Field(default="/tmp/sandbox", alias="SANDBOX_SHARED_MOUNT")

    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_org: str = Field(default="", alias="GITHUB_ORG")
    primary_language: str = Field(default="r", alias="PRIMARY_LANGUAGE")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def postgres_sync_dsn(self) -> str:
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
