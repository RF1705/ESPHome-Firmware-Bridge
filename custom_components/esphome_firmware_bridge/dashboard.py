"""Client for ESPHome Dashboard / Device Builder."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import logging
from typing import Any

from aiohttp import BasicAuth, ClientError, ClientSession, WSMsgType
from yarl import URL

_LOGGER = logging.getLogger(__name__)
_MESSAGE_IDS = itertools.count(1)


class ESPHomeDashboardError(Exception):
    """Raised when ESPHome Dashboard cannot complete a request."""


@dataclass(slots=True)
class DashboardNode:
    """Normalized ESPHome Dashboard node."""

    name: str
    filename: str
    address: str | None
    online: bool | None
    installed_version: str | None
    latest_version: str | None


class ESPHomeDashboardClient:
    """Small defensive client for ESPHome Dashboard endpoints."""

    def __init__(
        self,
        session: ClientSession,
        dashboard_url: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = URL(str(dashboard_url).rstrip("/"))
        self._username = username
        self._password = password or ""
        self._auth = BasicAuth(username, password or "") if username else None
        self._verify_ssl = verify_ssl

    async def async_get_nodes(self) -> list[DashboardNode]:
        """Return nodes known to ESPHome Dashboard."""
        try:
            data = await self._send_ws_command("devices/list")
        except ESPHomeDashboardError as err:
            _LOGGER.debug("ESPHome Dashboard WebSocket devices/list failed: %s", err)
            data = await self._request_json("GET", ("/devices", "/api/devices"))

        raw_nodes = self._extract_nodes(data)
        dashboard_version = await self.async_get_dashboard_version()

        nodes: list[DashboardNode] = []
        for raw in raw_nodes:
            node = self._normalize_node(raw, dashboard_version)
            if node is not None:
                nodes.append(node)

        return nodes

    async def async_get_dashboard_version(self) -> str | None:
        """Return the ESPHome Dashboard version if the endpoint exposes it."""
        try:
            data = await self._send_ws_command("config/version")
        except ESPHomeDashboardError:
            try:
                data = await self._request_json(
                    "GET", ("/version", "/info", "/api/info")
                )
            except ESPHomeDashboardError:
                return None

        if isinstance(data, str):
            return data
        if not isinstance(data, dict):
            return None

        for key in ("esphome_version", "version", "dashboard_version"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def async_install(self, node: DashboardNode) -> None:
        """Ask ESPHome Dashboard to build and OTA install a node."""
        configuration = node.filename or f"{node.name}.yaml"
        payload = {"configuration": configuration, "port": "OTA"}

        try:
            await self._send_ws_command("firmware/install", payload)
            return
        except ESPHomeDashboardError:
            _LOGGER.debug("ESPHome Dashboard WebSocket firmware/install failed")

        await self._request_json(
            "POST",
            (
                f"/devices/{configuration}/install",
                f"/api/devices/{configuration}/install",
                "/install",
                "/api/install",
                "/run",
                "/api/run",
            ),
            json=payload,
        )

    async def _send_ws_command(
        self, command: str, args: dict[str, Any] | None = None
    ) -> Any:
        """Send a Device Builder WebSocket command and return its result."""
        url = self._ws_url()
        message_id = str(next(_MESSAGE_IDS))

        try:
            async with self._session.ws_connect(
                url,
                ssl=self._verify_ssl,
                heartbeat=30,
            ) as websocket:
                server_message = await websocket.receive_json()
                if server_message.get("requires_auth"):
                    if not self._username:
                        raise ESPHomeDashboardError(
                            "ESPHome Dashboard requires authentication"
                        )
                    await websocket.send_json(
                        {
                            "command": "auth/login",
                            "message_id": f"{message_id}-auth",
                            "args": {
                                "username": self._username,
                                "password": self._password,
                            },
                        }
                    )
                    await self._receive_ws_result(websocket, f"{message_id}-auth")

                await websocket.send_json(
                    {
                        "command": command,
                        "message_id": message_id,
                        "args": args or {},
                    }
                )
                return await self._receive_ws_result(websocket, message_id)
        except (ClientError, TimeoutError, ValueError) as err:
            raise ESPHomeDashboardError(
                f"ESPHome Dashboard WebSocket request failed: {err}"
            ) from err

    @staticmethod
    async def _receive_ws_result(websocket, message_id: str) -> Any:
        """Receive a WebSocket result message for a command."""
        while True:
            message = await websocket.receive()
            if message.type == WSMsgType.TEXT:
                data = message.json()
                if data.get("message_id") != message_id:
                    continue
                if "error_code" in data:
                    raise ESPHomeDashboardError(
                        f"{data['error_code']}: {data.get('details', '')}"
                    )
                return data.get("result")
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                raise ESPHomeDashboardError("ESPHome Dashboard WebSocket closed")

    def _ws_url(self) -> URL:
        """Return the Device Builder WebSocket URL."""
        scheme = "wss" if self._base_url.scheme == "https" else "ws"
        return URL(f"{self._base_url.with_scheme(scheme)}/ws")

    async def _request_json(
        self,
        method: str,
        paths: tuple[str, ...],
        **kwargs: Any,
    ) -> Any:
        """Try multiple Dashboard endpoint shapes and return JSON."""
        last_error: Exception | None = None

        for path in paths:
            url = URL(f"{self._base_url}/{path.lstrip('/')}")
            try:
                async with self._session.request(
                    method,
                    url,
                    auth=self._auth,
                    ssl=self._verify_ssl,
                    **kwargs,
                ) as response:
                    if response.status == 404:
                        last_error = ESPHomeDashboardError(f"{method} {url} not found")
                        continue
                    if response.status >= 400:
                        body = await response.text()
                        raise ESPHomeDashboardError(
                            f"{method} {url} failed with {response.status}: {body}"
                        )
                    if response.content_type == "application/json":
                        return await response.json()
                    text = await response.text()
                    if not text:
                        return {}
                    raise ESPHomeDashboardError(
                        f"{method} {url} returned {response.content_type}"
                    )
            except (ClientError, TimeoutError, ESPHomeDashboardError) as err:
                last_error = err
                _LOGGER.debug("ESPHome Dashboard endpoint failed: %s", err)

        raise ESPHomeDashboardError(
            f"ESPHome Dashboard request failed: {last_error}"
        ) from last_error

    @staticmethod
    def _extract_nodes(data: Any) -> list[dict[str, Any]]:
        """Extract node dictionaries from common Dashboard responses."""
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []

        for key in ("devices", "nodes", "configured", "configurations"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        configured = data.get("configured")
        if isinstance(configured, dict):
            return [item for item in configured.values() if isinstance(item, dict)]

        return []

    @staticmethod
    def _normalize_node(
        raw: dict[str, Any], dashboard_version: str | None
    ) -> DashboardNode | None:
        """Normalize node fields from several ESPHome Dashboard generations."""
        name = _first_str(raw, "name", "node", "storage", "friendly_name")
        filename = _first_str(raw, "configuration", "filename", "path", "file")

        if not name and filename:
            name = filename.rsplit("/", 1)[-1].removesuffix(".yaml")
        if not filename and name:
            filename = f"{name}.yaml"
        if not name or not filename:
            return None

        return DashboardNode(
            name=name,
            filename=filename,
            address=_first_str(raw, "address", "ip", "host"),
            online=_first_bool(raw, "online", "is_online"),
            installed_version=_first_str(
                raw,
                "installed_version",
                "deployed_version",
                "current_version",
                "firmware_version",
                "esphome_version",
                "loaded_integrations_version",
            ),
            latest_version=_first_str(raw, "latest_version", "target_version")
            or dashboard_version,
        )


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string from a dictionary."""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_bool(data: dict[str, Any], *keys: str) -> bool | None:
    """Return the first boolean from a dictionary."""
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
    return None
