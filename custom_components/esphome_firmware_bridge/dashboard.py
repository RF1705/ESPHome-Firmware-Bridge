"""Client for ESPHome Dashboard / Device Builder."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
import logging
import time
from typing import Any

from aiohttp import (
    BasicAuth,
    ClientError,
    ClientSession,
    WSMsgType,
    WSServerHandshakeError,
)
from yarl import URL

_LOGGER = logging.getLogger(__name__)
_MESSAGE_IDS = itertools.count(1)
_TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
_VERSION_OVERRIDE_SECONDS = 600


class DeviceBuilderUnavailable(Exception):
    """Raised when the multiplexed Device Builder API is unavailable."""


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
        self._backend: str | None = None
        self._device_builder_version: str | None = None
        self._version_overrides: dict[str, tuple[str, float]] = {}

    async def async_get_nodes(self) -> list[DashboardNode]:
        """Return nodes known to ESPHome Dashboard."""
        if self._backend != "legacy":
            try:
                data, server_info = await self._device_builder_command("devices/list")
            except DeviceBuilderUnavailable as err:
                _LOGGER.debug("Device Builder API unavailable: %s", err)
                self._backend = "legacy"
            else:
                self._backend = "device_builder"
                self._device_builder_version = _first_str(
                    server_info,
                    "esphome_version",
                    "version",
                    "dashboard_version",
                )
                return self._normalize_nodes(data, self._device_builder_version)

        data = await self._request_json("GET", ("/devices", "/api/devices"))
        dashboard_version = await self.async_get_dashboard_version()
        return self._normalize_nodes(data, dashboard_version)

    def _normalize_nodes(
        self, data: Any, dashboard_version: str | None
    ) -> list[DashboardNode]:
        """Normalize a Dashboard or Device Builder node response."""
        raw_nodes = self._extract_nodes(data)
        nodes: list[DashboardNode] = []
        for raw in raw_nodes:
            node = self._normalize_node(raw, dashboard_version)
            if node is not None:
                nodes.append(node)

        self._apply_version_overrides(nodes)
        return nodes

    def _apply_version_overrides(self, nodes: list[DashboardNode]) -> None:
        """Apply recent successful install versions while discovery catches up."""
        now = time.monotonic()
        for node in nodes:
            override = self._version_overrides.get(node.filename)
            if override is None:
                continue

            version, expires_at = override
            if now >= expires_at or node.installed_version == version:
                self._version_overrides.pop(node.filename, None)
                continue

            node.installed_version = version

    async def async_get_dashboard_version(self) -> str | None:
        """Return the ESPHome Dashboard version if the endpoint exposes it."""
        if self._backend == "device_builder":
            return self._device_builder_version

        try:
            data = await self._request_json("GET", ("/version", "/info", "/api/info"))
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

        if self._backend is None:
            await self.async_get_nodes()

        if self._backend == "device_builder":
            await self._install_with_device_builder(configuration)
            self._remember_installed_version(configuration, node.latest_version)
            return

        await self._run_dashboard_command(
            "compile",
            {"configuration": configuration},
        )
        await self._run_dashboard_command(
            "upload",
            {"configuration": configuration, "port": "OTA"},
        )
        self._remember_installed_version(configuration, node.latest_version)

    def _remember_installed_version(
        self, configuration: str, version: str | None
    ) -> None:
        """Remember a successful flash until the backend reports the new version."""
        if not version:
            return
        self._version_overrides[configuration] = (
            version,
            time.monotonic() + _VERSION_OVERRIDE_SECONDS,
        )

    async def _install_with_device_builder(self, configuration: str) -> None:
        """Install firmware through the multiplexed Device Builder API."""
        compile_job, _ = await self._device_builder_command(
            "firmware/install",
            {"configuration": configuration, "port": "OTA"},
        )
        if not isinstance(compile_job, dict) or not isinstance(
            compile_job.get("job_id"), str
        ):
            raise ESPHomeDashboardError(
                "Device Builder returned an invalid firmware/install response"
            )

        compile_job_id = compile_job["job_id"]
        deadline = asyncio.get_running_loop().time() + 1800

        while asyncio.get_running_loop().time() < deadline:
            jobs, _ = await self._device_builder_command(
                "firmware/get_jobs",
                {"configuration": configuration},
            )
            if not isinstance(jobs, list):
                raise ESPHomeDashboardError(
                    "Device Builder returned an invalid firmware/get_jobs response"
                )

            related_jobs = [
                job
                for job in jobs
                if isinstance(job, dict)
                and (
                    job.get("job_id") == compile_job_id
                    or job.get("depends_on") == compile_job_id
                )
            ]
            compile_state = next(
                (
                    job
                    for job in related_jobs
                    if job.get("job_id") == compile_job_id
                ),
                compile_job,
            )
            upload_state = next(
                (
                    job
                    for job in related_jobs
                    if job.get("depends_on") == compile_job_id
                    and job.get("job_type") == "upload"
                ),
                None,
            )

            self._raise_for_failed_job(compile_state)
            if upload_state is not None:
                self._raise_for_failed_job(upload_state)
                if (
                    compile_state.get("status") == "completed"
                    and upload_state.get("status") == "completed"
                ):
                    return

            await asyncio.sleep(3)

        raise ESPHomeDashboardError(
            f"Device Builder firmware install timed out for {configuration}"
        )

    @staticmethod
    def _raise_for_failed_job(job: dict[str, Any]) -> None:
        """Raise a useful error for a failed or cancelled firmware job."""
        status = job.get("status")
        if status not in _TERMINAL_JOB_STATUSES or status == "completed":
            return

        output = job.get("output")
        tail = ""
        if isinstance(output, list):
            tail = "\n".join(
                line.strip()
                for line in output[-8:]
                if isinstance(line, str) and line.strip()
            )
        detail = job.get("error") or tail or f"exit code {job.get('exit_code')}"
        raise ESPHomeDashboardError(
            f"Device Builder {job.get('job_type', 'firmware')} job "
            f"{status}: {detail}"
        )

    async def _device_builder_command(
        self, command: str, args: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, Any]]:
        """Send one command through the multiplexed Device Builder WebSocket."""
        url = self._ws_url("ws")
        message_id = str(next(_MESSAGE_IDS))

        try:
            async with self._session.ws_connect(
                url,
                auth=self._auth,
                ssl=self._verify_ssl,
                heartbeat=30,
            ) as websocket:
                server_info = await self._receive_device_builder_json(websocket)
                if not isinstance(server_info, dict) or not (
                    "server_version" in server_info
                    or "esphome_version" in server_info
                ):
                    raise DeviceBuilderUnavailable(
                        "WebSocket did not return Device Builder server info"
                    )

                if server_info.get("requires_auth"):
                    if not self._username:
                        raise ESPHomeDashboardError(
                            "Device Builder requires authentication"
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
                    await self._receive_device_builder_result(
                        websocket, f"{message_id}-auth"
                    )

                await websocket.send_json(
                    {
                        "command": command,
                        "message_id": message_id,
                        "args": args or {},
                    }
                )
                result = await self._receive_device_builder_result(
                    websocket, message_id
                )
                return result, server_info
        except WSServerHandshakeError as err:
            if err.status in (400, 404):
                raise DeviceBuilderUnavailable(
                    f"Device Builder WebSocket endpoint returned {err.status}"
                ) from err
            raise ESPHomeDashboardError(
                f"Device Builder WebSocket handshake failed: {err}"
            ) from err
        except DeviceBuilderUnavailable:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise ESPHomeDashboardError(
                f"Device Builder WebSocket request failed: {err}"
            ) from err

    @staticmethod
    async def _receive_device_builder_json(websocket) -> Any:
        """Receive one JSON message from the Device Builder WebSocket."""
        message = await websocket.receive()
        if message.type == WSMsgType.TEXT:
            return message.json()
        raise DeviceBuilderUnavailable(
            f"Device Builder WebSocket closed with message type {message.type}"
        )

    @staticmethod
    async def _receive_device_builder_result(websocket, message_id: str) -> Any:
        """Wait for a Device Builder result or error message."""
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
                if "result" in data:
                    return data["result"]
                continue
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                raise ESPHomeDashboardError("Device Builder WebSocket closed")

    async def _run_dashboard_command(
        self, endpoint: str, payload: dict[str, Any]
    ) -> None:
        """Run a Dashboard command WebSocket and wait for its exit code."""
        url = self._ws_url(endpoint)
        install_log: list[str] = []

        try:
            async with self._session.ws_connect(
                url,
                auth=self._auth,
                ssl=self._verify_ssl,
                heartbeat=30,
            ) as websocket:
                await websocket.send_json({"type": "spawn", **payload})
                await self._wait_for_dashboard_command(websocket, install_log)
        except (ClientError, TimeoutError, ValueError) as err:
            raise ESPHomeDashboardError(
                f"ESPHome Dashboard WebSocket request failed: {err}"
            ) from err

    @staticmethod
    async def _wait_for_dashboard_command(websocket, install_log: list[str]) -> None:
        """Wait until a Dashboard command WebSocket exits."""
        while True:
            message = await websocket.receive()
            if message.type == WSMsgType.TEXT:
                data = message.json()
                event = data.get("event")
                if event == "line":
                    line = data.get("data")
                    if isinstance(line, str):
                        install_log.append(line.strip())
                    continue
                if event == "exit":
                    if data.get("code") == 0:
                        return
                    tail = "\n".join(line for line in install_log[-8:] if line)
                    raise ESPHomeDashboardError(
                        f"ESPHome command exited with code {data.get('code')}: {tail}"
                    )
                continue
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                raise ESPHomeDashboardError("ESPHome Dashboard WebSocket closed")

    def _ws_url(self, endpoint: str) -> URL:
        """Return a Dashboard command WebSocket URL."""
        scheme = "wss" if self._base_url.scheme == "https" else "ws"
        return URL(f"{self._base_url.with_scheme(scheme)}/{endpoint.lstrip('/')}")

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

        for key in (
            "devices",
            "nodes",
            "configured",
            "configurations",
            "entries",
            "items",
        ):
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

        runtime_state = raw.get("runtime_state")
        has_runtime_state = isinstance(runtime_state, dict)
        runtime_state = runtime_state if has_runtime_state else {}

        installed = _first_str(runtime_state, "deployed_version") or _first_str(
            raw,
            "installed_version",
            "deployed_version",
            "firmware_version",
            "esphome_version",
            "loaded_integrations_version",
        )
        if not has_runtime_state:
            installed = installed or _first_str(raw, "current_version")

        latest = _first_str(
            raw,
            "latest_version",
            "target_version",
            "available_version",
        )
        if has_runtime_state:
            latest = latest or _first_str(raw, "current_version")
        latest = latest or dashboard_version

        return DashboardNode(
            name=name,
            filename=filename,
            address=_first_str(raw, "address", "ip", "host"),
            online=_device_online(raw),
            installed_version=installed,
            latest_version=latest,
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


def _device_online(data: dict[str, Any]) -> bool | None:
    """Return online state from legacy booleans or Device Builder state."""
    if (online := _first_bool(data, "online", "is_online")) is not None:
        return online
    state = data.get("state")
    if state == "online":
        return True
    if state == "offline":
        return False
    return None
