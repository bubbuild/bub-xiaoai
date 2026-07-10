import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from mijiaAPI import LoginError as MijiaLoginError

from bub_xiaoai.mi import XiaoAiMessageListener, XiaoAiSettings, _login_with_mijia


class MijiaAuthMigrationTest(TestCase):
    def test_legacy_token_is_replaced_only_after_mijia_login_succeeds(self) -> None:
        with TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            auth_path.write_text(
                json.dumps({"userId": "legacy", "micoapi": ["", "token"]}),
                encoding="utf-8",
            )
            login_path = auth_path.with_name("auth.json.mijia")
            new_auth = {
                "cUserId": "current",
                "serviceToken": "token",
                "ssecurity": "security",
                "ua": "user-agent",
                "userId": "current",
            }
            api = MagicMock()

            def complete_login() -> dict[str, str]:
                login_path.write_text(json.dumps(new_auth), encoding="utf-8")
                return new_auth

            api.login.side_effect = complete_login
            with patch("bub_xiaoai.mi.MijiaAPI", return_value=api) as api_type:
                result = _login_with_mijia(auth_path)

            api_type.assert_called_once_with(str(login_path))
            self.assertEqual(result, new_auth)
            self.assertEqual(
                json.loads(auth_path.read_text(encoding="utf-8")), new_auth
            )
            self.assertFalse(login_path.exists())

    def test_legacy_token_is_preserved_when_mijia_login_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            legacy_auth = {"userId": "legacy"}
            auth_path.write_text(json.dumps(legacy_auth), encoding="utf-8")

            with patch("bub_xiaoai.mi.MijiaAPI") as api_type:
                api_type.return_value.login.side_effect = MijiaLoginError(-1, "failed")
                with self.assertRaises(MijiaLoginError):
                    _login_with_mijia(auth_path)

            self.assertEqual(
                json.loads(auth_path.read_text(encoding="utf-8")), legacy_auth
            )


class XiaoAiMessageListenerLoginTest(IsolatedAsyncioTestCase):
    async def test_login_uses_mijia_auth_before_requesting_micoapi_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            token_home = Path(temp_dir) / "auth.json"
            listener = XiaoAiMessageListener(XiaoAiSettings(token_home=token_home))
            listener._session = MagicMock()

            events: list[str] = []
            auth_data = {"userId": "user", "deviceId": "device", "passToken": "pass"}
            mijia_api = MagicMock()
            mico_client = MagicMock()
            mico_client.authenticate = AsyncMock(
                side_effect=lambda: events.append("micoapi")
            )

            with (
                patch(
                    "bub_xiaoai.mi._login_with_mijia",
                    side_effect=lambda path: events.append("mijia") or auth_data,
                ) as login,
                patch("bub_xiaoai.mi.MijiaAPI", return_value=mijia_api) as api_type,
                patch(
                    "bub_xiaoai.mi.MicoClient", return_value=mico_client
                ) as client_type,
            ):
                await listener._login()

            login.assert_called_once_with(token_home)
            api_type.assert_called_once_with(str(token_home))
            client_type.assert_called_once_with(listener.session, auth_data)
            mico_client.authenticate.assert_awaited_once_with()
            self.assertEqual(events, ["mijia", "micoapi"])

    async def test_login_skips_authentication_when_cookie_is_configured(self) -> None:
        listener = XiaoAiMessageListener(XiaoAiSettings(cookie="deviceId=test"))

        with (
            patch("bub_xiaoai.mi._login_with_mijia") as login,
            patch("bub_xiaoai.mi.MicoClient") as client_type,
        ):
            await listener._login()

        login.assert_not_called()
        client_type.assert_not_called()

    async def test_login_wraps_mijia_login_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            listener = XiaoAiMessageListener(
                XiaoAiSettings(token_home=Path(temp_dir) / "auth.json")
            )
            listener._session = MagicMock()

            with patch("bub_xiaoai.mi.MijiaAPI") as api_type:
                api_type.return_value.login.side_effect = MijiaLoginError(
                    -1, "timeout"
                )
                with self.assertRaisesRegex(RuntimeError, "QR login failed"):
                    await listener._login()

    async def test_login_reports_micoapi_exchange_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            listener = XiaoAiMessageListener(
                XiaoAiSettings(token_home=Path(temp_dir) / "auth.json")
            )
            listener._session = MagicMock()
            auth_data = {"userId": "user", "deviceId": "device", "passToken": "pass"}
            mico_client = MagicMock()
            mico_client.authenticate = AsyncMock(
                side_effect=RuntimeError("micoapi token exchange failed")
            )

            with (
                patch("bub_xiaoai.mi._login_with_mijia", return_value=auth_data),
                patch("bub_xiaoai.mi.MijiaAPI"),
                patch("bub_xiaoai.mi.MicoClient", return_value=mico_client),
            ):
                with self.assertRaisesRegex(RuntimeError, "token exchange failed"):
                    await listener._login()

    async def test_run_action_maps_hardware_command_to_mijia_api(self) -> None:
        listener = XiaoAiMessageListener(
            XiaoAiSettings(mi_did="123", hardware="LX06")
        )
        mijia_api = MagicMock()
        mijia_api.run_action.return_value = {"code": 0}
        listener._mijia_api = mijia_api

        result = await listener._run_action(listener.exec_command, ["hello", 0])

        mijia_api.run_action.assert_called_once_with(
            {
                "did": "123",
                "siid": 5,
                "aiid": 5,
                "value": ["hello", 0],
            }
        )
        self.assertEqual(result, {"code": 0})
