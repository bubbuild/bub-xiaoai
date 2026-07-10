from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
from typing import Any
from urllib import parse

from aiohttp import ClientSession

MICO_API_BASE_URL = "https://api2.mina.mi.com"
MICO_USER_AGENT = (
    "MiHome/6.0.103 (com.xiaomi.mihome; build:6.0.103.1; iOS 14.4.0) "
    "Alamofire/6.0.103 MICO/iOSApp/appStore/6.0.103"
)
PLAY_MUSIC_HARDWARE = frozenset(
    {
        "LX04",
        "LX05",
        "L05B",
        "L05C",
        "L06",
        "L06A",
        "X08A",
        "X10A",
        "X08C",
        "X08E",
        "X8F",
    }
)


class MicoClient:
    def __init__(
        self,
        session: ClientSession,
        auth_data: dict[str, Any],
    ) -> None:
        self._session = session
        self._auth_data = auth_data
        self._user_id = str(auth_data["userId"])
        self._service_token = ""
        self._device_hardware: dict[str, str] = {}

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def service_token(self) -> str:
        if not self._service_token:
            raise RuntimeError("micoapi client has not been authenticated")
        return self._service_token

    async def authenticate(self) -> None:
        response = await self._service_login("serviceLogin?sid=micoapi&_json=true")
        if response.get("code") != 0:
            raise RuntimeError(
                f"micoapi token exchange failed: {response.get('desc', response)}"
            )

        self._user_id = str(response["userId"])
        self._service_token = await self._security_token_service(
            response["location"],
            response["nonce"],
            response["ssecurity"],
        )

    async def _service_login(self, uri: str) -> dict[str, Any]:
        cookies = {
            "sdkVersion": "3.9",
            "deviceId": str(self._auth_data["deviceId"]),
            "userId": str(self._auth_data["userId"]),
            "passToken": str(self._auth_data["passToken"]),
        }
        async with self._session.get(
            f"https://account.xiaomi.com/pass/{uri}",
            cookies=cookies,
            headers={"User-Agent": str(self._auth_data.get("ua", MICO_USER_AGENT))},
            ssl=False,
        ) as response:
            response.raise_for_status()
            payload = await response.text()
        return json.loads(payload.removeprefix("&&&START&&&"))

    async def _security_token_service(
        self,
        location: str,
        nonce: int,
        ssecurity: str,
    ) -> str:
        sign_source = f"nonce={nonce}&{ssecurity}"
        client_sign = base64.b64encode(
            hashlib.sha1(sign_source.encode()).digest()
        ).decode()
        url = f"{location}&clientSign={parse.quote(client_sign)}"
        async with self._session.get(url) as response:
            response.raise_for_status()
            service_token = response.cookies.get("serviceToken")
            if service_token is None or not service_token.value:
                raise RuntimeError("micoapi response did not include a serviceToken")
            return service_token.value

    async def request(
        self,
        uri: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = "app_ios_" + "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(30)
        )
        if data is None:
            separator = "&" if "?" in uri else "?"
            uri = f"{uri}{separator}requestId={request_id}"
        else:
            data = {**data, "requestId": request_id}

        async with self._session.request(
            "GET" if data is None else "POST",
            MICO_API_BASE_URL + uri,
            data=data,
            cookies={
                "userId": self.user_id,
                "serviceToken": self.service_token,
            },
            headers={"User-Agent": MICO_USER_AGENT},
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if payload.get("code") != 0:
            raise RuntimeError(f"micoapi request failed for {uri}: {payload}")
        return payload

    async def device_list(self) -> list[dict[str, Any]]:
        response = await self.request("/admin/v2/device_list?master=0")
        devices = response.get("data") or []
        self._device_hardware.update(
            {
                item["deviceID"]: item["hardware"]
                for item in devices
                if item.get("deviceID") and item.get("hardware")
            }
        )
        return devices

    async def ubus_request(
        self,
        device_id: str,
        method: str,
        path: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.request(
            "/remote/ubus",
            {
                "deviceId": device_id,
                "message": json.dumps(message),
                "method": method,
                "path": path,
            },
        )

    async def text_to_speech(self, device_id: str, text: str) -> dict[str, Any]:
        return await self.ubus_request(
            device_id, "text_to_speech", "mibrain", {"text": text}
        )

    async def player_get_status(self, device_id: str) -> dict[str, Any]:
        return await self.ubus_request(
            device_id,
            "player_get_play_status",
            "mediaplayer",
            {"media": "app_ios"},
        )

    async def player_pause(self, device_id: str) -> dict[str, Any]:
        return await self.ubus_request(
            device_id,
            "player_play_operation",
            "mediaplayer",
            {"action": "pause", "media": "app_ios"},
        )

    async def play_by_url(
        self,
        device_id: str,
        url: str,
        media_type: int = 1,
    ) -> dict[str, Any]:
        if device_id not in self._device_hardware:
            await self.device_list()
        if self._device_hardware.get(device_id) not in PLAY_MUSIC_HARDWARE:
            return await self.ubus_request(
                device_id,
                "player_play_url",
                "mediaplayer",
                {"url": url, "type": media_type, "media": "app_ios"},
            )

        audio_id = "1582971365183456177"
        music = {
            "payload": {
                "audio_type": "MUSIC" if media_type == 1 else "",
                "audio_items": [
                    {
                        "item_id": {
                            "audio_id": audio_id,
                            "cp": {
                                "album_id": "-1",
                                "episode_index": 0,
                                "id": "355454500",
                                "name": "xiaowei",
                            },
                        },
                        "stream": {"url": url},
                    }
                ],
                "list_params": {
                    "listId": "-1",
                    "loadmore_offset": 0,
                    "origin": "xiaowei",
                    "type": "MUSIC",
                },
            },
            "play_behavior": "REPLACE_ALL",
        }
        return await self.ubus_request(
            device_id,
            "player_play_music",
            "mediaplayer",
            {"startaudioid": audio_id, "music": json.dumps(music)},
        )
