"""Unit tests — base URL scheme validation.

Production incident 2026-08-23: LLM_BASE_URL was set without a scheme, so
openai.AsyncOpenAI received it verbatim and httpx raised UnsupportedProtocol on
every call, surfacing as a bare "Connection error." after the SDK's retries.

The damage was in how quietly it failed: the app booted, /ready passed,
requests returned 200 OK, and both the supervisor and response-generation LLM
calls failed on every turn — users got fallback text indefinitely while the
service looked healthy. Same branch in dev was fine because its LLM_BASE_URL
was empty.

These settings are optional (empty = use the provider's default endpoint), so a
non-empty value without a scheme can only be an operator error.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

_DB = "postgresql+asyncpg://u:p@h:5432/d"


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, DATABASE_URL=_DB, **env)  # type: ignore[arg-type]


class TestBaseUrlSchemeRequired:
    @pytest.mark.parametrize("var", ["LLM_BASE_URL", "EMBEDDING_BASE_URL"])
    def test_scheme_less_value_is_rejected(self, var: str) -> None:
        with pytest.raises(ValueError) as excinfo:
            _settings(**{var: "api.openai.com/v1"})
        message = str(excinfo.value)
        assert var in message
        assert "http://" in message and "https://" in message
        # The offending value is echoed so the operator can see the typo.
        assert "api.openai.com/v1" in message

    @pytest.mark.parametrize("var", ["LLM_BASE_URL", "EMBEDDING_BASE_URL"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_is_allowed_and_normalised_to_none(self, var: str, blank: str) -> None:
        """Empty means "use the provider default" — the working dev config."""
        settings = _settings(**{var: blank})
        assert getattr(settings, var.lower()) is None

    def test_unset_is_allowed(self) -> None:
        settings = _settings()
        assert settings.llm_base_url is None
        assert settings.embedding_base_url is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "http://internal-gateway:8080/v1",
        ],
    )
    def test_valid_schemes_pass_through(self, url: str) -> None:
        """http:// is kept as-is — an internal endpoint must not be rewritten."""
        assert _settings(LLM_BASE_URL=url).llm_base_url == url

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert (
            _settings(LLM_BASE_URL="  https://api.openai.com/v1  ").llm_base_url
            == "https://api.openai.com/v1"
        )


class TestWhitespaceOnlyValuesAreTreatedAsUnset:
    """Key Vault cannot store an empty secret, so "no value" gets saved as a
    single space — and a space is truthy in Python, so it sails past every
    ``value or default`` guard and reaches clients as real configuration.

    LLM_BASE_URL=' ' became URL('%20/') inside the OpenAI client and took every
    LLM call down; EMBEDDING_BASE_URL=' ' did the same to RAG. A blank value now
    means the same thing as the variable being absent.
    """

    @pytest.mark.parametrize(
        ("var", "attr", "expected"),
        [
            # The two that caused the outage — optional, so they fall back to None
            # and the SDK uses its own default endpoint.
            ("LLM_BASE_URL", "llm_base_url", None),
            ("EMBEDDING_BASE_URL", "embedding_base_url", None),
            # These would have failed differently: the registry raises
            # "Unknown LLM provider ' '" rather than a connection error.
            ("LLM_PROVIDER", "llm_provider", "openai"),
            ("EMBEDDING_PROVIDER", "embedding_provider", "openai"),
            ("LLM_MODEL", "llm_model", "gpt-4o-mini"),
        ],
    )
    @pytest.mark.parametrize("blank", [" ", "   ", "\t", "\n"])
    def test_blank_falls_back_to_the_default(
        self, var: str, attr: str, expected: object, blank: str
    ) -> None:
        assert getattr(_settings(**{var: blank}), attr) == expected

    def test_real_values_are_never_touched(self) -> None:
        settings = _settings(
            LLM_PROVIDER="azure_openai",
            LLM_MODEL="gpt-5.4-mini",
            LLM_BASE_URL="https://api.openai.com/v1",
        )
        assert settings.llm_provider == "azure_openai"
        assert settings.llm_model == "gpt-5.4-mini"
        assert settings.llm_base_url == "https://api.openai.com/v1"

    def test_no_setting_can_hold_whitespace(self) -> None:
        """Sweep every env-aliased setting; none may survive as blank."""
        import re
        from pathlib import Path

        source = Path("app/core/config.py").read_text()
        aliases = sorted(set(re.findall(r'validation_alias="([A-Z_]+)"', source)))
        assert aliases, "no aliases found — the scan is broken, not the config"

        survivors = []
        for alias in aliases:
            env = {"DATABASE_URL": _DB, alias: " "}
            try:
                settings = Settings(_env_file=None, **env)  # type: ignore[arg-type]
            except ValueError:
                continue  # rejected outright — also acceptable
            value = getattr(settings, alias.lower(), None)
            if isinstance(value, str) and not value.strip():
                survivors.append(alias)
        assert survivors == [], f"still hold whitespace: {survivors}"
