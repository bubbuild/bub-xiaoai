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
            self.assertEqual(json.loads(auth_path.read_text(encoding="utf-8")), new_auth)
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
            mijia_api = MagicMock()
            mijia_api.login.side_effect = lambda: events.append("mijia")
            account = MagicMock()
            account.login = AsyncMock(
                side_effect=lambda sid: events.append(sid) or True
            )

            with (
                patch("bub_xiaoai.mi.MijiaAPI", return_value=mijia_api) as api_type,
                patch("bub_xiaoai.mi.MiAccount", return_value=account) as account_type,
            ):
                await listener._login()

            api_type.assert_called_once_with(str(token_home))
            account_type.assert_called_once()
            account.login.assert_awaited_once_with("micoapi")
            self.assertEqual(events, ["mijia", "micoapi"])

    async def test_login_skips_authentication_when_cookie_is_configured(self) -> None:
        listener = XiaoAiMessageListener(XiaoAiSettings(cookie="deviceId=test"))

        with (
            patch("bub_xiaoai.mi.MijiaAPI") as api_type,
            patch("bub_xiaoai.mi.MiAccount") as account_type,
        ):
            await listener._login()

        api_type.assert_not_called()
        account_type.assert_not_called()

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
            account = MagicMock()
            account.login = AsyncMock(return_value=False)

            with (
                patch("bub_xiaoai.mi.MijiaAPI") as api_type,
                patch("bub_xiaoai.mi.MiAccount", return_value=account),
            ):
                api_type.return_value.login.return_value = {}
                with self.assertRaisesRegex(RuntimeError, "micoapi login failed"):
                    await listener._login()
