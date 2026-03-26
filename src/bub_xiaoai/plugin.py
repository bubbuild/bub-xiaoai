from bub import BubFramework, hookimpl, tool
from bub.builtin.settings import load_settings
from bub.channels import Channel
from bub.types import Envelope, MessageHandler, State
from republic import ToolContext

from bub_xiaoai.channel import XiaoAiChannel
from bub_xiaoai.mi import XiaoAiMessageListener, XiaoAiSettings


def _get_xiaoai(context: ToolContext) -> XiaoAiMessageListener:
    if "_runtime_xiaoai" not in context.state:
        raise RuntimeError("XiaoAiMessageListener not found in context")
    return context.state["_runtime_xiaoai"]


class XiaoAiPlugin:
    def __init__(self, framework: BubFramework) -> None:
        settings = load_settings()
        self.listener = XiaoAiMessageListener(
            XiaoAiSettings(mi_token_home=settings.home / "mi_token.json")
        )

    @hookimpl
    def load_state(self, message: Envelope, session_id: str) -> State:
        return {"_runtime_xiaoai": self.listener}

    @hookimpl
    def provide_channels(self, message_handler: MessageHandler) -> list[Channel]:
        return [XiaoAiChannel(message_handler, self.listener)]


@tool(name="xiaoai.speak", context=True)
async def xiaoai_speak(context: ToolContext, text: str) -> None:
    """Make a TTS request to XiaoAi."""
    listener = _get_xiaoai(context)
    await listener.speak(text)


@tool(name="xiaoai.play", context=True)
async def xiaoai_play(context: ToolContext, url_or_file: str) -> None:
    """Play a media URL or file on XiaoAi."""
    listener = _get_xiaoai(context)
    await listener.play_url_or_file(url_or_file)
