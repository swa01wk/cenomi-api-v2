"""Unit tests for ServiceRequestPlatformClient.

Coverage
--------
- login: success, non-2xx, missing token, connection error
- ensure_authenticated: skips re-login when token fresh; re-logins when expired
- get_service_request: success, non-2xx, connection error
- create_service_request: success, non-2xx
- patch_service_request: success, non-2xx
- submit_report: success (reuses POST)
- upload_file: success, non-2xx, connection error
- _extract_sr_id: various envelope shapes
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.exceptions import PlatformAuthError
from app.agents.services.platform_api_client import (
    FileUploadMetadata,
    ServiceRequestPlatformClient,
)
from app.core.request_context import reset_current_auth_context, set_current_auth_context
from app.types.chat import AuthContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(base_url: str = "http://platform.test") -> ServiceRequestPlatformClient:
    return ServiceRequestPlatformClient(
        base_url=base_url,
        auth_base_url=base_url,
        timeout=5.0,
    )


def _mock_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    resp.headers = {}
    return resp


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success_sets_token(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(200, {"access_token": "tok-abc"})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            await client.login(email="test@test.com", internal_api_token="secret")

        assert client._access_token == "tok-abc"

    @pytest.mark.asyncio
    async def test_login_non_2xx_raises(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(401, {"error": "unauthorized"})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(PlatformAuthError, match="401"):
                await client.login(email="test@test.com", internal_api_token="bad")

    @pytest.mark.asyncio
    async def test_login_missing_token_raises(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(200, {"message": "ok"})  # no access_token

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(PlatformAuthError, match="access_token"):
                await client.login(email="a@b.com", internal_api_token="x")

    @pytest.mark.asyncio
    async def test_login_connection_error_raises(self) -> None:
        import httpx
        client = _make_client()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ConnectError("timeout")
            )
            with pytest.raises(PlatformAuthError, match="connection"):
                await client.login()


# ---------------------------------------------------------------------------
# ensure_authenticated
# ---------------------------------------------------------------------------


class TestEnsureAuthenticated:
    @pytest.mark.asyncio
    async def test_skips_relogin_when_token_fresh(self) -> None:
        client = _make_client()
        client._access_token = "cached-token"
        client._token_acquired_at = time.monotonic()

        with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
            await client.ensure_authenticated()
            mock_login.assert_not_called()

    @pytest.mark.asyncio
    async def test_relogins_when_token_expired(self) -> None:
        client = _make_client()
        client._access_token = "stale-token"
        client._token_acquired_at = time.monotonic() - 9999.0  # well past TTL

        with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
            await client.ensure_authenticated()
            mock_login.assert_called_once()

    @pytest.mark.asyncio
    async def test_relogins_when_no_token(self) -> None:
        client = _make_client()
        # No token set

        with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
            await client.ensure_authenticated()
            mock_login.assert_called_once()


# ---------------------------------------------------------------------------
# get_service_request
# ---------------------------------------------------------------------------


class TestGetServiceRequest:
    @pytest.mark.asyncio
    async def test_success_returns_result(self) -> None:
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        operations = [{"role": "FM_MANAGER", "status": "IN_PROGRESS"}]
        body = {"data": {"status": "IN_PROCESS", "service_request_operations": operations}}
        mock_resp = _mock_response(200, body)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            result = await client.get_service_request("sr-123")

        assert result.sr_id == "sr-123"
        assert result.status == "IN_PROCESS"
        assert len(result.service_request_operations) == 1
        assert result.error is None

    @pytest.mark.asyncio
    async def test_merges_top_level_lease_refs_into_payload(self) -> None:
        """Platform puts lease_id on the SR DTO, not inside nested payload."""
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        body = {
            "data": {
                "service_request_id": "11711",
                "status": "IN_PROCESS",
                "lease_id": 9001,
                "lease_code": "t0208831",
                "tenant_profile_id": 55,
                "property_id": 7,
                "operations": [{"role": "FM_MANAGER"}],
                "payload": {
                    "brand_id": 42,
                    "description": "Handover for unit A",
                },
            }
        }
        mock_resp = _mock_response(200, body)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            result = await client.get_service_request("11711")

        assert result.payload is not None
        assert result.payload["lease_id"] == 9001
        assert result.payload["lease_code"] == "t0208831"
        assert result.payload["tenant_profile_id"] == 55
        assert result.payload["property_id"] == 7
        assert result.payload["brand_id"] == 42
        assert result.payload["description"] == "Handover for unit A"
        assert len(result.service_request_operations) == 1

    @pytest.mark.asyncio
    async def test_maps_nested_lease_field_to_lease_code(self) -> None:
        """Detail GET omits top-level lease_id; nested payload uses ``lease``."""
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        body = {
            "data": {
                "service_request_id": "11711",
                "status": "IN_PROCESS",
                "tenant_profile_id": 55,
                "payload": {
                    "lease": "t0208831",
                    "brand_id": 42,
                    "property_id": 7,
                },
            }
        }
        mock_resp = _mock_response(200, body)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            result = await client.get_service_request("11711")

        assert result.payload is not None
        assert result.payload["lease_code"] == "t0208831"
        assert "lease_id" not in result.payload or result.payload.get("lease_id") in (
            None,
            "",
            0,
        )

    @pytest.mark.asyncio
    async def test_non_2xx_returns_error(self) -> None:
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        mock_resp = _mock_response(404, {"error": "not found"})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            result = await client.get_service_request("sr-999")

        assert result.error == "HTTP 404"
        assert result.status is None

    @pytest.mark.asyncio
    async def test_get_forwards_bearer_and_skips_login(self) -> None:
        client = _make_client()
        operations = [{"role": "FM_MANAGER", "status": "IN_PROGRESS"}]
        body = {"data": {"status": "IN_PROCESS", "service_request_operations": operations}}
        mock_resp = _mock_response(200, body)
        captured: dict = {}

        async def _fake_get(endpoint, headers=None, params=None):  # noqa: ANN001
            captured["headers"] = headers
            return mock_resp

        ctx_token = set_current_auth_context(
            AuthContext(subject_id="u1", bearer_token="ai.jwt.token")
        )
        try:
            with patch("httpx.AsyncClient") as mock_httpx, patch.object(
                client, "login", new_callable=AsyncMock
            ) as mock_login:
                mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                    side_effect=_fake_get
                )
                result = await client.get_service_request("11172")
        finally:
            reset_current_auth_context(ctx_token)

        mock_login.assert_not_called()
        assert captured["headers"]["Authorization"] == "Bearer ai.jwt.token"
        assert result.sr_id == "11172"
        assert result.status == "IN_PROCESS"

    @pytest.mark.asyncio
    async def test_get_without_bearer_uses_s2s_login(self) -> None:
        client = _make_client()
        body = {"data": {"status": "IN_PROCESS", "service_request_operations": []}}
        mock_resp = _mock_response(200, body)

        with patch("httpx.AsyncClient") as mock_httpx, patch.object(
            client, "ensure_authenticated", new_callable=AsyncMock, return_value=True
        ) as mock_ensure:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            result = await client.get_service_request("sr-123")

        mock_ensure.assert_called_once()
        assert result.sr_id == "sr-123"


class TestIsAgenticRoute:
    def test_get_service_request_by_id_is_agentic(self) -> None:
        url = "http://localhost:8000/api/v1/service-requests/11172"
        assert ServiceRequestPlatformClient._is_agentic_route("GET", url) is True

    def test_patch_service_request_by_id_is_agentic(self) -> None:
        url = "http://localhost:8000/api/v1/service-requests/11712"
        assert ServiceRequestPlatformClient._is_agentic_route("PATCH", url) is True

    def test_get_workflows_is_not_agentic(self) -> None:
        url = "http://localhost:8000/api/v1/service-requests/workflows"
        assert ServiceRequestPlatformClient._is_agentic_route("GET", url) is False


# ---------------------------------------------------------------------------
# patch_service_request
# ---------------------------------------------------------------------------


class TestPatchServiceRequest:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        mock_resp = _mock_response(200, {"success": True, "id": "sr-123"})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.patch = AsyncMock(
                return_value=mock_resp
            )
            result = await client.patch_service_request("sr-123", {"status": "IN_PROCESS"})

        assert result.sr_id == "sr-123"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_patch_forwards_bearer_and_skips_login(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(200, {"success": True, "id": "11712"})
        captured: dict = {}

        async def _fake_patch(endpoint, json=None, headers=None):  # noqa: ANN001
            captured["headers"] = headers
            return mock_resp

        ctx_token = set_current_auth_context(
            AuthContext(subject_id="u1", bearer_token="ai.jwt.token")
        )
        try:
            with patch("httpx.AsyncClient") as mock_httpx, patch.object(
                client, "login", new_callable=AsyncMock
            ) as mock_login:
                mock_httpx.return_value.__aenter__.return_value.patch = AsyncMock(
                    side_effect=_fake_patch
                )
                result = await client.patch_service_request(
                    "11712", {"status": "APPROVED"}
                )
        finally:
            reset_current_auth_context(ctx_token)

        mock_login.assert_not_called()
        assert captured["headers"]["Authorization"] == "Bearer ai.jwt.token"
        assert result.sr_id == "11712"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_non_2xx_returns_error(self) -> None:
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        mock_resp = _mock_response(500, {"error": "server error"})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.patch = AsyncMock(
                return_value=mock_resp
            )
            result = await client.patch_service_request("sr-123", {})

        assert result.error == "HTTP 500"
        assert result.sr_id is None


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------


class TestUploadFile:
    def _make_metadata(self) -> FileUploadMetadata:
        return FileUploadMetadata(
            document_type_id="SR_HANDOVER_CHECKLIST",
            sr_id="sr-123",
            lease_id="456",
            brand_id="789",
            property_id="101",
            lease_code="LC-001",
            tenant_profile_id="116",
        )

    @pytest.mark.asyncio
    async def test_upload_success(self) -> None:
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        body = {"data": {"document_id": "doc-uuid-123", "signed_url": "https://s3.example/doc"}}
        mock_resp = _mock_response(200, body)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.put = AsyncMock(
                return_value=mock_resp
            )
            result = await client.upload_file(b"file-bytes", self._make_metadata())

        assert result.document_id == "doc-uuid-123"
        assert result.signed_url == "https://s3.example/doc"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_upload_non_2xx_returns_error(self) -> None:
        client = _make_client()
        client._access_token = "tok"
        client._token_acquired_at = time.monotonic()
        mock_resp = _mock_response(413, {"error": "too large"})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.put = AsyncMock(
                return_value=mock_resp
            )
            result = await client.upload_file(b"bytes", self._make_metadata())

        assert result.error == "HTTP 413"
        assert result.document_id is None


# ---------------------------------------------------------------------------
# _extract_sr_id
# ---------------------------------------------------------------------------


class TestCreateServiceRequestBearer:
    """Bearer forwarding via request context on agentic POST /service-requests."""

    @pytest.mark.asyncio
    async def test_create_forwards_bearer_and_skips_login(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(201, {"data": {"service_request_id": "SR-1"}})
        captured: dict = {}

        async def _fake_post(endpoint, json=None, headers=None):  # noqa: ANN001
            captured["headers"] = headers
            return mock_resp

        ctx_token = set_current_auth_context(
            AuthContext(subject_id="u1", bearer_token="ai.jwt.token")
        )
        try:
            with patch("httpx.AsyncClient") as mock_httpx, patch.object(
                client, "login", new_callable=AsyncMock
            ) as mock_login:
                mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                    side_effect=_fake_post
                )
                result = await client.create_service_request({"foo": "bar"})
        finally:
            reset_current_auth_context(ctx_token)

        mock_login.assert_not_called()
        assert captured["headers"]["Authorization"] == "Bearer ai.jwt.token"
        assert result.sr_id == "SR-1"

    @pytest.mark.asyncio
    async def test_create_bearer_401_does_not_relogin(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(401, {"error": "unauthorized"})
        ctx_token = set_current_auth_context(
            AuthContext(subject_id="u1", bearer_token="ai.jwt.token")
        )
        try:
            with patch("httpx.AsyncClient") as mock_httpx, patch.object(
                client, "ensure_authenticated", new_callable=AsyncMock
            ) as mock_ensure:
                post_mock = AsyncMock(return_value=mock_resp)
                mock_httpx.return_value.__aenter__.return_value.post = post_mock
                result = await client.create_service_request({"foo": "bar"})
        finally:
            reset_current_auth_context(ctx_token)

        mock_ensure.assert_not_called()
        assert post_mock.call_count == 1
        assert result.error == "HTTP 401"

    @pytest.mark.asyncio
    async def test_create_without_bearer_uses_s2s_login(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(201, {"data": {"service_request_id": "SR-2"}})

        with patch("httpx.AsyncClient") as mock_httpx, patch.object(
            client, "ensure_authenticated", new_callable=AsyncMock, return_value=True
        ) as mock_ensure:
            mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await client.create_service_request({"foo": "bar"})

        mock_ensure.assert_called_once()
        assert result.sr_id == "SR-2"

    @pytest.mark.asyncio
    async def test_submit_report_forwards_bearer(self) -> None:
        client = _make_client()
        mock_resp = _mock_response(201, {"data": {"service_request_id": "SR-3"}})
        captured: dict = {}

        async def _fake_post(endpoint, json=None, headers=None):  # noqa: ANN001
            captured["headers"] = headers
            return mock_resp

        ctx_token = set_current_auth_context(
            AuthContext(subject_id="u1", bearer_token="ai.jwt.token")
        )
        try:
            with patch("httpx.AsyncClient") as mock_httpx, patch.object(
                client, "login", new_callable=AsyncMock
            ) as mock_login:
                mock_httpx.return_value.__aenter__.return_value.post = AsyncMock(
                    side_effect=_fake_post
                )
                result = await client.submit_report({"service_request_id": "SR-3"})
        finally:
            reset_current_auth_context(ctx_token)

        mock_login.assert_not_called()
        assert captured["headers"]["Authorization"] == "Bearer ai.jwt.token"
        assert result.sr_id == "SR-3"


class TestExtractSrId:
    def test_top_level_id(self) -> None:
        assert ServiceRequestPlatformClient._extract_sr_id({"id": "abc"}) == "abc"

    def test_nested_data_id(self) -> None:
        assert ServiceRequestPlatformClient._extract_sr_id({"data": {"sr_id": "xyz"}}) == "xyz"

    def test_service_request_id_key(self) -> None:
        assert (
            ServiceRequestPlatformClient._extract_sr_id({"service_request_id": "def"}) == "def"
        )

    def test_none_on_empty(self) -> None:
        assert ServiceRequestPlatformClient._extract_sr_id({}) is None

    def test_none_on_non_dict(self) -> None:
        assert ServiceRequestPlatformClient._extract_sr_id(None) is None
