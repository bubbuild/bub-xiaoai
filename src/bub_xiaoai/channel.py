from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from bub.channels import Channel, ChannelMessage
from bub.types import MessageHandler
from loguru import logger

from .mi import WAKEUP_KEYWORD, XiaoAiMessageListener


class XiaoAiChannel(Channel):
    name = "xiaoai"

    def __init__(self, on_receive: MessageHandler, listener: XiaoAiMessageListener):
        self.listener = listener
        self.on_receive = on_receive
        self._ongoing_task: asyncio.Task | None = None

    @asynccontextmanager
    async def _in_processing(self) -> AsyncIterator[None]:
        await self.listener.stop_if_xiaoai_is_playing()
        yield
        await self.listener.wait_for_tts_finish()
        with suppress(Exception):
            await self.listener.wakeup_xiaoai()
            await self.listener.stop_if_xiaoai_is_playing()

    async def start(self, stop_event: asyncio.Event) -> None:
        self._ongoing_task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        if self._ongoing_task is not None:
            self._ongoing_task.cancel()
            try:
                await self._ongoing_task
            except asyncio.CancelledError:
                pass
            self._ongoing_task = None

    def _build_message(self, message: dict[str, Any]) -> ChannelMessage:
        content = message["query"].strip()
        chat_id = self.listener.config.chat_id

        return ChannelMessage(
            session_id=f"{self.name}:{chat_id}",
            channel=self.name,
            chat_id=chat_id,
            content=content,
            output_channel="null",
            lifespan=self._in_processing(),
        )

    async def _main_loop(self) -> None:
        logger.info("channel.xiaoai: started listening for messages")
        try:
            async with self.listener as listener:
                async for msg in listener.listen():
                    query = msg["query"].strip()
                    if query == WAKEUP_KEYWORD:
                        continue
                    logger.info("channel.xiaoai: received message: {}", query)
                    await self.on_receive(self._build_message(msg))
        finally:
            logger.info("channel.xiaoai: stopped listening for messages")
