#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bub_xiaoai.mi import XiaoAiMessageListener, XiaoAiSettings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify mijia and micoapi authentication for XiaoAi."
    )
    parser.add_argument(
        "--auth-path",
        type=Path,
        help=(
            "Authentication file path. Defaults to BUB_MI_TOKEN_HOME or the "
            "XiaoAiSettings default."
        ),
    )
    return parser.parse_args()


async def verify_authentication(auth_path: Path | None) -> None:
    settings = XiaoAiSettings()
    if auth_path is not None:
        settings.token_home = auth_path.expanduser()
    if settings.cookie:
        raise RuntimeError(
            "BUB_MI_COOKIE is set; unset it to test mijiaAPI QR authentication"
        )

    listener = XiaoAiMessageListener(settings)
    try:
        print(f"Using authentication file: {settings.token_home}")
        print("Authenticating with mijiaAPI and exchanging the micoapi token...")
        await listener.authenticate()

        print("Checking access to Mi Home...")
        homes = await listener._call_mijia(listener.mijia_api.get_homes_list)

        print("Checking access to XiaoAi Mina devices...")
        devices = await listener.mico_client.device_list()
        matching_devices = [
            device
            for device in devices
            if device.get("hardware") == settings.hardware
        ]

        print("Authentication succeeded.")
        print(f"Mi Home households: {len(homes)}")
        print(f"XiaoAi devices: {len(devices)}")
        print(
            f"Devices matching configured hardware {settings.hardware}: "
            f"{len(matching_devices)}"
        )
    finally:
        await listener.close()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(verify_authentication(args.auth_path))
    except KeyboardInterrupt:
        print("Authentication cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
