"""Application configuration via environment variables."""

from functools import lru_cache
from typing import Any

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="service-request-chatbot-api", validation_alias="APP_NAME")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    api_v1_prefix: str = Field(default="/api/v1", validation_alias="API_V1_PREFIX")
    api_v2_prefix: str = Field(default="/api/v2", validation_alias="API_V2_PREFIX")

    cors_origins: str = Field(
        default="http://localhost:3000",
        validation_alias="CORS_ORIGINS",
        description="Comma-separated list of allowed browser origins.",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/service_request_chatbot",
        validation_alias="DATABASE_URL",
        description="Async SQLAlchemy URL, e.g. postgresql+asyncpg://...",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    service_request_api_base_url: str | None = Field(
        default=None, validation_alias="SERVICE_REQUEST_API_BASE_URL"
    )
    lease_tenant_api_base_url: str | None = Field(
        default=None, validation_alias="LEASE_TENANT_API_BASE_URL"
    )
    tenant_portal_base_url: str | None = Field(
        default=None,
        validation_alias="TENANT_PORTAL_BASE_URL",
        description=(
            "Base URL for Tenant Central (no trailing slash). Used to build deep "
            "links to existing service-request details pages when lease lookup "
            "returns reason=handover_exists. When unset, relative paths are used."
        ),
    )
    lease_lookup_use_mock: bool = Field(
        default=False,
        validation_alias="LEASE_LOOKUP_USE_MOCK",
        description=(
            "Force in-memory mock lease data even when LEASE_TENANT_API_BASE_URL is set. "
            "Use for local eval runs where the UAT API rejects the chatbot's local JWT."
        ),
    )
    service_request_use_mock: bool = Field(
        default=False,
        validation_alias="SERVICE_REQUEST_USE_MOCK",
        description=(
            "Force in-memory mock SR state even when SERVICE_REQUEST_API_BASE_URL is set. "
            "Use for local eval runs where the UAT API rejects the chatbot's local JWT."
        ),
    )
    notifications_use_mock: bool = Field(
        default=False,
        validation_alias="NOTIFICATIONS_USE_MOCK",
        description=(
            "Force in-memory mock notification inbox data even when the platform "
            "base URL and internal API token are configured. Use for local dev/eval."
        ),
    )
    # FILE_UPLOAD_API_BASE_URL is accepted in .env for forward compatibility but
    # is not currently used — file uploads route through SERVICE_REQUEST_API_BASE_URL
    # + PUT /files per the Postman collection.  Keeping the field avoids noisy
    # "extra fields not permitted" errors if operators set it.
    file_upload_api_base_url: str | None = Field(
        default=None,
        validation_alias="FILE_UPLOAD_API_BASE_URL",
        description=(
            "Reserved for future use. File uploads currently use "
            "SERVICE_REQUEST_API_BASE_URL + /files (per Postman collection)."
        ),
    )

    # ── Platform auth (service-to-service; separate from user JWT) ────────────

    # ``platform_base_url`` is an alias for ``service_request_api_base_url``
    # kept for clarity in platform_api_client and related modules.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def platform_base_url(self) -> str | None:
        return self.service_request_api_base_url

    platform_auth_base_url: str | None = Field(
        default=None,
        validation_alias="PLATFORM_AUTH_BASE_URL",
        description=(
            "Base URL for the Cenomi platform auth endpoint "
            "(POST /cenomi-ai/login). Defaults to SERVICE_REQUEST_API_BASE_URL when absent."
        ),
    )
    platform_internal_api_token: str | None = Field(
        default=None,
        validation_alias="PLATFORM_INTERNAL_API_TOKEN",
        description="x-internal-api-token header value for the platform login call.",
    )
    platform_login_email: str | None = Field(
        default=None,
        validation_alias="PLATFORM_LOGIN_EMAIL",
        description="Email address used for service-to-service platform login.",
    )

    # ── MSP Platform integration ──────────────────────────────────────────────
    msp_service_token: str | None = Field(
        default=None,
        validation_alias="MSP_SERVICE_TOKEN",
        description=(
            "Inbound x-internal-api-token value expected from the MSP Platform frontend. "
            "When set, /api/v1/chat and /api/v1/chat/stream reject requests that omit or "
            "mismatch this token.  When absent (default), validation is skipped — "
            "shadow mode, matching the RBAC_ENFORCE=false pattern."
        ),
    )

    jwt_secret_key: str = Field(default="change-me", validation_alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, validation_alias="JWT_EXPIRE_MINUTES")
    rbac_enforce: bool = Field(
        default=False,
        validation_alias="RBAC_ENFORCE",
        description="True = reject requests without a valid JWT. False = shadow mode (warn, pass through).",
    )

    # ── LLM / AI ──────────────────────────────────────────────────────────────
    llm_provider: str = Field(
        default="openai",
        validation_alias="LLM_PROVIDER",
        description="LLM backend: 'openai' (default), 'azure_openai', or 'vertex_ai'.",
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API key used by LLMGateway (required in production).",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        validation_alias="LLM_MODEL",
        description="Chat model passed to LLMGateway (must support json_object response_format).",
    )
    llm_base_url: str | None = Field(
        default=None,
        validation_alias="LLM_BASE_URL",
        description="Optional custom base URL for the OpenAI-compatible endpoint.",
    )
    @model_validator(mode="before")
    @classmethod
    def _blank_optional_to_none(cls, data: Any) -> Any:
        """Treat a whitespace-only value as unset, for every optional setting.

        Azure Key Vault cannot store an empty secret value, so "this one has no
        value" gets saved as a single space. A space is truthy in Python, so it
        sails straight past the ``value or default`` guards these settings rely
        on and is handed to clients as though it were real configuration.

        That took production down on 2026-08-23: LLM_BASE_URL=' ' became
        URL('%20/') inside the OpenAI client, every LLM call failed with
        UnsupportedProtocol, and EMBEDDING_BASE_URL=' ' did the same to RAG —
        while the app booted, /ready passed and requests returned 200 OK.

        Dropping the key here lets the field's own default apply, which is what
        "unset" already means when the variable is simply absent. That also
        rescues the non-URL settings: LLM_PROVIDER=' ' would otherwise reach the
        registry as an unknown provider and raise instead of defaulting to
        "openai". Required settings are left alone so a genuinely missing value
        still fails loudly.
        """
        if not isinstance(data, dict):
            return data

        optional: set[str] = set()
        for name, field in cls.model_fields.items():
            # Anything with a default: blank is indistinguishable from absent,
            # so give it the same meaning. Fields with no default stay put, so a
            # genuinely required setting still fails validation rather than
            # silently acquiring a value nobody chose.
            if field.is_required():
                continue
            optional.add(name)
            alias = field.validation_alias
            if isinstance(alias, str):
                optional.add(alias)

        return {
            key: value
            for key, value in data.items()
            if not (isinstance(value, str) and not value.strip() and key in optional)
        }

    @field_validator("llm_base_url", "embedding_base_url")
    @classmethod
    def _require_url_scheme(cls, value: str | None, info: Any) -> str | None:
        """Reject a base URL with no scheme; normalise blank to None.

        These are optional — empty means "use the provider's own default
        endpoint" — so a non-empty value without http:// or https:// can only be
        an operator error. Left unchecked it reaches httpx as-is and raises
        UnsupportedProtocol on every call, which surfaces as a bare
        "Connection error." after the OpenAI SDK's retries.

        That failure is invisible where it matters: the app boots, /ready passes,
        requests return 200 OK, and every LLM call fails, so users get fallback
        text indefinitely while the service looks healthy. Refusing to start
        makes the mistake immediate and names the fix.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.startswith(("http://", "https://")):
            env_var = info.field_name.upper()
            raise ValueError(
                f"{env_var} must start with http:// or https:// (got {stripped!r}). "
                f"Leave {env_var} empty to use the provider's default endpoint."
            )
        return stripped

    handover_lease_picker_enabled: bool = Field(
        default=True,
        validation_alias="HANDOVER_LEASE_PICKER_ENABLED",
        description=(
            "Offer browsable eligible-lease chips when the Handover CREATE flow "
            "has no lease identifiers yet. The picker shows the 5 most recently "
            "activated leases with a search box and a read-more pager, so it "
            "answers 'which lease?' instead of listing leases at random. Set "
            "false to fall back to the plain lease-code question."
        ),
    )
    handover_short_flow_enabled: bool = Field(
        default=False,
        validation_alias="HANDOVER_SHORT_FLOW_ENABLED",
        description=(
            "Collapse the Path A CREATE_SR ask loop from nine questions to "
            "four turns: handover type, one combined inspection question "
            "(start date + end date + inspector), documents, then preview. "
            "Set false to keep the field-per-turn flow."
        ),
    )
    handover_ai_autofill_enabled: bool = Field(
        default=False,
        validation_alias="HANDOVER_AI_AUTOFILL_ENABLED",
        description=(
            "Let an LLM draft the handover description and notes instead of "
            "using the deterministic templates. Requires "
            "HANDOVER_SHORT_FLOW_ENABLED; falls back to the templates on any "
            "error or timeout. The title is always template-generated."
        ),
    )
    llm_confidence_threshold: float = Field(
        default=0.6,
        validation_alias="LLM_CONFIDENCE_THRESHOLD",
        description="Minimum supervisor confidence before routing; below this asks clarification.",
    )

    # ── LLM — Azure OpenAI (when LLM_PROVIDER=azure_openai) ───────────────────
    azure_openai_llm_deployment: str | None = Field(
        default=None,
        validation_alias="AZURE_OPENAI_LLM_DEPLOYMENT",
        description="Azure OpenAI deployment name for chat completions (defaults to LLM_MODEL when absent).",
    )

    # ── LLM / Embedding — Google Cloud Vertex AI ──────────────────────────────
    # Shared by both LLM_PROVIDER=vertex_ai and EMBEDDING_PROVIDER=vertex_ai.
    vertex_ai_project: str | None = Field(
        default=None,
        validation_alias="VERTEX_AI_PROJECT",
        description="Google Cloud project ID for Vertex AI (LLM and/or embeddings).",
    )
    vertex_ai_location: str = Field(
        default="us-central1",
        validation_alias="VERTEX_AI_LOCATION",
        description="Google Cloud region for Vertex AI endpoints (LLM and/or embeddings).",
    )
    vertex_ai_credentials_path: str | None = Field(
        default=None,
        validation_alias="VERTEX_AI_CREDENTIALS_PATH",
        description="Optional path to GCP service account JSON key file (defaults to ADC).",
    )
    vertex_ai_embedding_model: str = Field(
        default="text-embedding-005",
        validation_alias="VERTEX_AI_EMBEDDING_MODEL",
        description="Vertex AI embedding model name (only when EMBEDDING_PROVIDER=vertex_ai).",
    )

    # ── RAG provider selection ────────────────────────────────────────────────
    embedding_provider: str = Field(
        default="openai",
        validation_alias="EMBEDDING_PROVIDER",
        description="Embedding backend: 'openai' (default), 'azure_openai', or 'vertex_ai'.",
    )
    search_provider: str = Field(
        default="azure_search",
        validation_alias="SEARCH_PROVIDER",
        description="Search backend: 'azure_search' (only current impl).",
    )

    # Embeddings — standard OpenAI (reuses existing openai_api_key)
    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="EMBEDDING_MODEL",
        description="Embedding model name (used by both OpenAI and Azure OpenAI providers).",
    )
    embedding_base_url: str | None = Field(
        default=None,
        validation_alias="EMBEDDING_BASE_URL",
        description="Optional custom base URL for OpenAI-compatible embedding endpoint.",
    )

    # Embeddings — Azure OpenAI (only when EMBEDDING_PROVIDER=azure_openai)
    azure_ai_endpoint: str | None = Field(
        default=None,
        validation_alias="AZURE_AI_ENDPOINT",
        description="Azure OpenAI endpoint, e.g. https://<instance>.openai.azure.com",
    )
    azure_ai_api_key: str | None = Field(
        default=None,
        validation_alias="AZURE_AI_API_KEY",
        description="Azure OpenAI API key (only when EMBEDDING_PROVIDER=azure_openai).",
    )
    azure_ai_api_version: str = Field(
        default="2024-02-15-preview",
        validation_alias="AZURE_AI_API_VERSION",
        description="Azure OpenAI API version for embedding calls.",
    )

    # Search — Azure AI Search (only when SEARCH_PROVIDER=azure_search)
    azure_search_endpoint: str | None = Field(
        default=None,
        validation_alias="AZURE_SEARCH_ENDPOINT",
        description="Azure AI Search endpoint, e.g. https://<instance>.search.windows.net",
    )
    azure_search_api_key: str | None = Field(
        default=None,
        validation_alias="AZURE_SEARCH_API_KEY",
        description="Azure AI Search admin/query key.",
    )
    azure_search_index_name: str | None = Field(
        default=None,
        validation_alias="AZURE_SEARCH_INDEX_NAME",
        description="Azure AI Search index name (e.g. cenomi-help-index).",
    )

    # ── RAG Knowledge Indexer job (ticket 3547) ──────────────────────────────
    # All optional/defaulted — the API process must start without any of them.
    # The indexer reads the Cenomi platform DB (read-only, raw SQL) and writes
    # to the same Azure AI Search index the read path queries.  Note the job
    # needs an ADMIN search key, unlike the query key the API uses.
    cenomi_database_url: str | None = Field(
        default=None,
        validation_alias="CENOMI_DATABASE_URL",
        description=(
            "Read-only Cenomi platform Postgres DSN, e.g. "
            "postgresql+asyncpg://ro_user:...@host:5432/cenomi?ssl=require. "
            "NOT the chatbot DATABASE_URL."
        ),
    )
    indexer_chunk_max_tokens: int = Field(
        default=500,
        validation_alias="INDEXER_CHUNK_MAX_TOKENS",
        description="Maximum tokens per indexed chunk (tiktoken cl100k_base).",
    )
    indexer_chunk_overlap: int = Field(
        default=50,
        validation_alias="INDEXER_CHUNK_OVERLAP",
        description="Token overlap between consecutive chunks; must be < max tokens.",
    )
    indexer_embed_batch_size: int = Field(
        default=50,
        validation_alias="INDEXER_EMBED_BATCH_SIZE",
        description="Texts per embed_batch call.",
    )
    indexer_upload_batch_size: int = Field(
        default=1000,
        validation_alias="INDEXER_UPLOAD_BATCH_SIZE",
        description="Documents per Azure Search upload batch.",
    )
    indexer_max_concurrency: int = Field(
        default=4,
        validation_alias="INDEXER_MAX_CONCURRENCY",
        description="Concurrent embed batch calls (semaphore).",
    )
    indexer_dry_run: bool = Field(
        default=False,
        validation_alias="INDEXER_DRY_RUN",
        description="Build + log documents; no Search writes; no watermark advance.",
    )
    indexer_enable_user_guides: bool = Field(
        default=False,
        validation_alias="INDEXER_ENABLE_USER_GUIDES",
        description="Enable the user_guide PDF source.",
    )
    indexer_enable_download_center: bool = Field(
        default=False,
        validation_alias="INDEXER_ENABLE_DOWNLOAD_CENTER",
        description="Enable the download_center PDF source.",
    )
    indexer_watermark_lag: int = Field(
        default=300,
        validation_alias="INDEXER_WATERMARK_LAG",
        description="Seconds subtracted from run start when advancing a watermark.",
    )
    indexer_max_docs_per_run: int = Field(
        default=5000,
        validation_alias="INDEXER_MAX_DOCS_PER_RUN",
        description="Incremental row cap per source per run.",
    )
    indexer_mall_info_full_on_incremental: bool = Field(
        default=False,
        validation_alias="INDEXER_MALL_INFO_FULL_ON_INCREMENTAL",
        description="Force mall_info full rebuild on incremental runs (updated_at unmaintained).",
    )
    indexer_blob_connection_string: str | None = Field(
        default=None,
        validation_alias="INDEXER_BLOB_CONNECTION_STRING",
        description="Azure Blob connection string for PDF sources.",
    )
    indexer_blob_container: str | None = Field(
        default=None,
        validation_alias="INDEXER_BLOB_CONTAINER",
        description="Blob container for PDF sources; blob name = str(document_id).",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
