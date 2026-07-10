import json
from http.cookies import SimpleCookie
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from bub_xiaoai.xiaomi import MicoClient


class FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        payload: dict | None = None,
        cookies: SimpleCookie | None = None,
    ) -> None:
        self._text = text
        self._payload = payload or {}
        self.cookies = cookies or SimpleCookie()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return self._text

    async def json(self, *, content_type=None) -> dict:
        return self._payload


class MicoClientTest(IsolatedAsyncioTestCase):
    async def test_authenticate_exchanges_pass_token_for_service_token(self) -> None:
        auth_data = {
            "deviceId": "passport-device",
            "passToken": "pass-token",
            "ua": "test-user-agent",
            "userId": "user-id",
        }
        login_payload = {
            "code": 0,
            "location": "https://account.example/callback?foo=bar",
            "nonce": 42,
            "ssecurity": "security",
            "userId": "current-user",
        }
        token_cookies = SimpleCookie()
        token_cookies["serviceToken"] = "mico-token"
        session = MagicMock()
        session.get.side_effect = [
            FakeResponse(text="&&&START&&&" + json.dumps(login_payload)),
            FakeResponse(cookies=token_cookies),
        ]
        client = MicoClient(session, auth_data)

        await client.authenticate()

        self.assertEqual(client.user_id, "current-user")
        self.assertEqual(client.service_token, "mico-token")
        first_call = session.get.call_args_list[0]
        self.assertIn("sid=micoapi", first_call.args[0])
        self.assertEqual(first_call.kwargs["cookies"]["passToken"], "pass-token")
        self.assertEqual(first_call.kwargs["headers"]["User-Agent"], "test-user-agent")
        self.assertIn("clientSign=", session.get.call_args_list[1].args[0])

    async def test_ubus_request_uses_mico_credentials(self) -> None:
        session = MagicMock()
        session.request.return_value = FakeResponse(payload={"code": 0, "data": {}})
        client = MicoClient(
            session,
            {
                "deviceId": "passport-device",
                "passToken": "pass-token",
                "userId": "user-id",
            },
        )
        client._service_token = "mico-token"

        await client.text_to_speech("speaker-id", "hello")

        call = session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertTrue(call.args[1].endswith("/remote/ubus"))
        self.assertEqual(call.kwargs["cookies"]["serviceToken"], "mico-token")
        self.assertEqual(call.kwargs["data"]["deviceId"], "speaker-id")
        self.assertEqual(call.kwargs["data"]["method"], "text_to_speech")
        self.assertEqual(
            json.loads(call.kwargs["data"]["message"]), {"text": "hello"}
        )
