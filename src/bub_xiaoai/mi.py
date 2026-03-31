from __future__ import annotations

import asyncio
import json
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, AsyncIterator

from aiohttp import ClientSession, ClientTimeout
from loguru import logger
from miservice import MiAccount, MiIOService, MiNAService, miio_command
from pydantic_settings import BaseSettings, SettingsConfigDict

from .static_server import TempStaticFileServer

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


class XiaoAiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUB_MI_", extra="ignore")

    hardware: str = "LX01"
    account: str = ""
    password: str = ""
    mi_did: str = ""
    cookie: str = ""
    mi_token_home: Path = DEFAULT_MI_TOKEN_HOME
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
        self._mina_service: MiNAService | None = None
        self._miio_service: MiIOService | None = None
        self._lock = asyncio.Lock()
        self.static_server = TempStaticFileServer()

    async def __aenter__(self) -> XiaoAiMessageListener:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def mina_service(self) -> MiNAService:
        if self._mina_service is None:
            raise RuntimeError("listener has not been started")
        return self._mina_service

    @property
    def miio_service(self) -> MiIOService:
        if self._miio_service is None:
            raise RuntimeError("listener has not been started")
        return self._miio_service

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
        self._session = ClientSession()
        try:
            await self.static_server.start()
            await self._login()
            await self._init_hardware()
            self._cookie_header = self._build_cookie_header()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        await self.static_server.close()

        if self._session is not None:
            await self._session.close()
            self._session = None

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            message = await self.fetch_latest_message()
            if (
                message is not None
                and message.get("query", "").strip() != WAKEUP_KEYWORD
            ):
                yield message
            await asyncio.sleep(self.config.poll_interval)

    async def fetch_latest_message(self) -> dict[str, Any] | None:
        async with self._lock:
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
        account = MiAccount(
            self.session,
            self.config.account,
            self.config.password,
            str(self.config.mi_token_home),
        )
        ok = await account.login("micoapi")
        if not ok:
            raise RuntimeError(
                "xiaomi login failed; verify MI_USER/MI_PASS, complete any Xiaomi "
                "security verification in the account app, or use --cookie instead"
            )
        self._mina_service = MiNAService(account)
        self._miio_service = MiIOService(account)

    async def _init_hardware(self) -> None:
        if self.config.cookie:
            return

        hardware_data = await self.mina_service.device_list()
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

        devices = await self.miio_service.device_list()
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

        if not self.config.mi_token_home.exists():
            raise RuntimeError(
                f"token file not found: {self.config.mi_token_home}; login did not "
                "produce a usable token file"
            )

        with self.config.mi_token_home.open(encoding="utf-8") as file:
            user_data = json.load(file)
        try:
            user_id = user_data["userId"]
            service_token = user_data["micoapi"][1]
        except KeyError as exc:
            raise RuntimeError(
                f"invalid Xiaomi token file: missing {exc.args[0]}"
            ) from exc
        return COOKIE_TEMPLATE.format(
            device_id=self.device_id,
            service_token=service_token,
            user_id=user_id,
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
        playing_info = await self.mina_service.player_get_status(self.device_id)
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
            await self.mina_service.player_pause(self.device_id)

    @property
    def tts_command(self) -> str:
        return HARDWARE_COMMAND_DICT.get(self.config.hardware, DEFAULT_COMMAND)[0]

    @property
    def exec_command(self) -> str:
        return HARDWARE_COMMAND_DICT.get(self.config.hardware, DEFAULT_COMMAND)[1]

    async def speak(self, text: str) -> None:
        """Make a TTS request to XiaoAi."""
        try:
            await self.mina_service.text_to_speech(self.device_id, text)
        except Exception:
            await miio_command(
                self.miio_service, self.config.mi_did, f"{self.tts_command} {text}"
            )

    async def execute(self, text: str, silent: bool = False) -> None:
        """Execute a command on XiaoAi."""
        await miio_command(
            self.miio_service,
            self.config.mi_did,
            f"{self.exec_command} {text} {0 if silent else 1}",
        )
        if text.strip().lower() == WAKEUP_KEYWORD.strip().lower():
            return
        # skip the next message
        async for message in self.listen():
            if message.get("query", "").strip().lower() == text.strip().lower():
                break

    async def wakeup_xiaoai(self) -> None:
        await miio_command(
            self.miio_service,
            self.config.mi_did,
            f"{self.exec_command} {WAKEUP_KEYWORD} 0",
        )

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
        await self.mina_service.play_by_url(self.device_id, url, _type=1)


def _parse_cookie_string(cookie_string: str) -> dict[str, str]:
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    return {key: morsel.value for key, morsel in cookie.items()}
