from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, AsyncIterator, TypeVar

from aiohttp import ClientSession, ClientTimeout
from loguru import logger
from mijiaAPI import LoginError as MijiaLoginError
from mijiaAPI import mijiaAPI as MijiaAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from .static_server import TempStaticFileServer
from .xiaomi import MicoClient

LATEST_ASK_API = (
    "https://userprofile.mina.mi.com/device_profile/v2/conversation"
    "?source=dialogu&hardware={hardware}&timestamp={timestamp}&limit=2"
)
COOKIE_TEMPLATE = "deviceId={device_id}; serviceToken={service_token}; userId={user_id}"
DEFAULT_MI_TOKEN_HOME = Path.home() / ".mi.token"
WAKEUP_KEYWORD = "小爱同学"
HARDWARE_COMMAND_DICT = {
    # hardware: (tts_command, wakeup_command)
    "LX06": ("5-1", "5-5"),
    "L05B": ("5-3", "5-4"),
    "S12": ("5-1", "5-5"),  # 第一代小爱，型号 MDZ-25-DA
    "S12A": ("5-1", "5-5"),
    "LX01": ("5-1", "5-5"),
    "L06A": ("5-1", "5-5"),
    "LX04": ("5-1", "5-4"),
    "L05C": ("5-3", "5-4"),
    "L17A": ("7-3", "7-4"),
    "X08E": ("7-3", "7-4"),
    "LX05A": ("5-1", "5-5"),  # 小爱红外版
    "LX5A": ("5-1", "5-5"),  # 小爱红外版
    "L07A": ("5-1", "5-5"),  # Redmi 小爱音箱 Play(l7a)
    "L15A": ("7-3", "7-4"),
    "X6A": ("7-3", "7-4"),  # 小米智能家庭屏 6
    "X10A": ("7-3", "7-4"),  # 小米智能家庭屏 10
    # add more here
}

DEFAULT_COMMAND = ("5-1", "5-5")
T = TypeVar("T")
MIJIA_AUTH_KEYS = frozenset(
    {"cUserId", "serviceToken", "ssecurity", "ua", "userId"}
)


def _login_with_mijia(auth_path: Path) -> dict[str, Any]:
    login_path = auth_path
    if auth_path.exists():
        with auth_path.open(encoding="utf-8") as file:
            auth_data = json.load(file)
        if not MIJIA_AUTH_KEYS.issubset(auth_data):
            # Keep a legacy token intact until QR authentication succeeds.
            login_path = auth_path.with_name(f"{auth_path.name}.mijia")

    auth_data = MijiaAPI(str(login_path)).login()
    if login_path != auth_path:
        login_path.replace(auth_path)
    return auth_data


class XiaoAiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUB_MI_", extra="ignore")

    hardware: str = "LX01"
    mi_did: str = ""
    cookie: str = ""
    token_home: Path = DEFAULT_MI_TOKEN_HOME
    poll_interval: float = 1.0
    request_timeout: float = 15.0
    chat_id: str = "xiaoai-chat"


class XiaoAiMessageListener:
    def __init__(self, config: XiaoAiSettings):
        self.config = config
        self.device_id = ""
        self.last_timestamp = int(time.time() * 1000)
        self._cookie_header = ""
        self._session: ClientSession | None = None
        self._mico_client: MicoClient | None = None
        self._mijia_api: MijiaAPI | None = None
        self._mijia_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self.static_server = TempStaticFileServer()

    async def __aenter__(self) -> XiaoAiMessageListener:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def mico_client(self) -> MicoClient:
        if self._mico_client is None:
            raise RuntimeError("listener has not been started")
        return self._mico_client

    @property
    def mijia_api(self) -> MijiaAPI:
        if self._mijia_api is None:
            raise RuntimeError("listener has not been started")
        return self._mijia_api

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("listener has not been started")
        return self._session

    @property
    def temp_dir(self) -> Path:
        return self.static_server.temp_dir

    @property
    def static_server_origin(self) -> str:
        return self.static_server.origin

    async def start(self) -> None:
        if self._session is not None:
            return
        try:
            self._session = ClientSession()
            await self.static_server.start()
            await self.authenticate()
            await self._init_hardware()
            self._cookie_header = self._build_cookie_header()
        except Exception:
            await self.close()
            raise

    async def authenticate(self) -> None:
        if self._session is None:
            self._session = ClientSession()
        if self.config.cookie or self._mico_client is not None:
            return
        await self._login()

    async def close(self) -> None:
        await self.static_server.close()

        if self._mijia_api is not None:
            await asyncio.to_thread(self._mijia_api.session.close)
            self._mijia_api = None
        self._mico_client = None

        if self._session is not None:
            await self._session.close()
            self._session = None

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            async with self._lock:
                message = await self.fetch_latest_message()
            if (
                message is not None
                and message.get("query", "").strip() != WAKEUP_KEYWORD
            ):
                yield message
            await asyncio.sleep(self.config.poll_interval)

    async def fetch_latest_message(self) -> dict[str, Any] | None:
        timeout = ClientTimeout(total=self.config.request_timeout)
        response = await self.session.get(
            LATEST_ASK_API.format(
                hardware=self.config.hardware,
                timestamp=int(time.time() * 1000),
            ),
            headers={"Cookie": self._cookie_header},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = await response.json()
        return self._extract_message(payload)

    async def _login(self) -> None:
        if self.config.cookie:
            return

        try:
            auth_data = await asyncio.to_thread(
                _login_with_mijia, self.config.token_home
            )
        except MijiaLoginError as exc:
            raise RuntimeError(
                "xiaomi QR login failed; scan the QR code with the Mi Home app "
                "and retry"
            ) from exc

        self._mijia_api = MijiaAPI(str(self.config.token_home))
        mico_client = MicoClient(self.session, auth_data)
        await mico_client.authenticate()
        self._mico_client = mico_client

    async def _init_hardware(self) -> None:
        if self.config.cookie:
            return

        hardware_data = await self.mico_client.device_list()
        for item in hardware_data:
            if self.config.mi_did and item.get("miotDID", "") == str(
                self.config.mi_did
            ):
                self.device_id = item.get("deviceID", "")
                break
            if item.get("hardware", "") == self.config.hardware:
                self.device_id = item.get("deviceID", "")
                break

        if not self.device_id:
            raise RuntimeError(
                f"cannot find device_id for hardware={self.config.hardware!r}; "
                "set mi_did explicitly if multiple devices exist"
            )

        if self.config.mi_did:
            return

        devices = await self._call_mijia(self.mijia_api.get_devices_list)
        for device in devices:
            if device.get("model", "").endswith(self.config.hardware.lower()):
                self.config.mi_did = str(device["did"])
                return
        raise RuntimeError(f"cannot find mi_did for hardware={self.config.hardware!r}")

    def _build_cookie_header(self) -> str:
        if self.config.cookie:
            cookies = _parse_cookie_string(self.config.cookie)
            if "deviceId" not in cookies:
                raise RuntimeError("cookie must include deviceId")
            self.device_id = cookies["deviceId"]
            return self.config.cookie

        return COOKIE_TEMPLATE.format(
            device_id=self.device_id,
            service_token=self.mico_client.service_token,
            user_id=self.mico_client.user_id,
        )

    def _extract_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        raw_data = payload.get("data")
        if not raw_data:
            return None

        records = json.loads(raw_data).get("records") or []
        if not records:
            return None

        record = records[0]
        timestamp = int(record.get("time", 0))
        if timestamp <= self.last_timestamp:
            return None

        self.last_timestamp = timestamp
        return record

    async def get_if_xiaoai_is_playing(self):
        playing_info = await self.mico_client.player_get_status(self.device_id)
        # WTF xiaomi api
        is_playing = (
            json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1)
            == 1
        )
        return is_playing

    async def stop_if_xiaoai_is_playing(self):
        is_playing = await self.get_if_xiaoai_is_playing()
        if is_playing:
            logger.debug("Muting xiaoai")
            # stop it
            await self.mico_client.player_pause(self.device_id)

    @property
    def tts_command(self) -> str:
        return HARDWARE_COMMAND_DICT.get(self.config.hardware, DEFAULT_COMMAND)[0]

    @property
    def exec_command(self) -> str:
        return HARDWARE_COMMAND_DICT.get(self.config.hardware, DEFAULT_COMMAND)[1]

    async def speak(self, text: str) -> None:
        """Make a TTS request to XiaoAi."""
        try:
            await self.mico_client.text_to_speech(self.device_id, text)
        except Exception:
            await self._run_action(self.tts_command, [text])

    async def execute(self, text: str, silent: bool = False) -> None:
        """Execute a command on XiaoAi."""
        async with self._lock:
            await self._run_action(self.exec_command, [text, 0 if silent else 1])
            # skip the next message
            while True:
                message = await self.fetch_latest_message()
                if (
                    message is not None
                    and message.get("query", "").lower().strip() == text.lower().strip()
                ):
                    break
                await asyncio.sleep(self.config.poll_interval)

    async def wakeup_xiaoai(self) -> None:
        await self._run_action(self.exec_command, [WAKEUP_KEYWORD, 0])

    async def wait_for_tts_finish(self):
        while True:
            if not await self.get_if_xiaoai_is_playing():
                break
            await asyncio.sleep(1)

    async def play_url_or_file(self, url_or_file: str) -> None:
        """Play a media URL or file on XiaoAi."""
        if "://" in url_or_file:
            url = url_or_file
        else:
            url = self.static_server.file_url(url_or_file)
        await self.mico_client.play_by_url(self.device_id, url, media_type=1)

    async def _call_mijia(
        self,
        method: Callable[..., T],
        *args: Any,
    ) -> T:
        async with self._mijia_lock:
            return await asyncio.to_thread(method, *args)

    async def _run_action(self, command: str, values: list[Any]) -> dict[str, Any]:
        siid, aiid = (int(part) for part in command.split("-", maxsplit=1))
        result = await self._call_mijia(
            self.mijia_api.run_action,
            {
                "did": self.config.mi_did,
                "siid": siid,
                "aiid": aiid,
                "value": values,
            },
        )
        if result.get("code", -1) not in (0, 1):
            raise RuntimeError(f"mijia action failed: {result}")
        return result


def _parse_cookie_string(cookie_string: str) -> dict[str, str]:
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    return {key: morsel.value for key, morsel in cookie.items()}
