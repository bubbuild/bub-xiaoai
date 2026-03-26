# Bub 🧡 XiaoAi

`bub-xiaoai` is a [Bub](https://github.com/bubbuild/bub) channel plugin for Xiaomi XiaoAi speakers.

It is based on the Xiaomi device integration approach from [`yihong0618/xiaogpt`](https://github.com/yihong0618/xiaogpt), but the scope here is narrower:

- listen for messages from a XiaoAi speaker
- forward them into Bub as a channel
- expose `xiaoai.speak` so the assistant can speak replies back through the speaker
- keep the interaction in continuous conversation mode by default

This project does not provide a standalone CLI runner for XiaoAi. It is meant to be loaded by Bub as a plugin.

## Features

- Message polling from XiaoAi via Xiaomi Mina APIs
- TTS playback through the XiaoAi speaker
- Automatic wake-up after a reply to keep conversation going
- Temporary static file HTTP server started with the listener lifecycle
- Bub tool `xiaoai.speak`
- Bundled skill `xiaoai` under `src/skills/xiaoai` to instruct the LLM to call `xiaoai.speak`

## How it works

At runtime the plugin creates a single `XiaoAiMessageListener`, registers a Bub channel named `xiaoai`, and exposes a tool:

- Channel: `xiaoai`
- Tool: `xiaoai.speak(text: str)`

Incoming XiaoAi queries are turned into Bub `ChannelMessage` objects. The assistant should answer by calling `xiaoai.speak`, which performs TTS on the device.
When the listener starts, it also creates a temporary directory and serves it through a local HTTP server for the same lifecycle window. The directory is available through `listener.static_server.temp_dir`, and the base URL is available through `listener.static_server.origin`.

The channel currently runs in continuous conversation mode by default:

- ordinary spoken queries are forwarded without a keyword prefix
- the raw wake word `小爱同学` is ignored and not sent to the LLM
- after a reply finishes, the plugin attempts to wake XiaoAi again so the next utterance can be captured

## Installation

### Requirements

- Python `>=3.13`

### Install the plugin to your Bub environment:

```bash
uv pip install git+https://github.com/frostming/bub-xiaoai.git
```

## Configuration

Settings are loaded from environment variables through `XiaoAiSettings` with prefix `BUB_MI_`.

Supported variables:

- `BUB_MI_HARDWARE`
- `BUB_MI_ACCOUNT`
- `BUB_MI_PASSWORD`
- `BUB_MI_MI_DID`
- `BUB_MI_COOKIE`
- `BUB_MI_MI_TOKEN_HOME`
- `BUB_MI_POLL_INTERVAL`
- `BUB_MI_REQUEST_TIMEOUT`
- `BUB_MI_CHAT_ID`

Defaults are defined in [src/bub_xiaoai/mi.py](/home/frost/workspace/bub-xiaoai/src/bub_xiaoai/mi.py).

### Authentication

Two login modes are supported:

1. Xiaomi account + password
2. Xiaomi cookie

If `BUB_MI_COOKIE` is set, the plugin skips account login and uses the cookie directly.

If you use account login, the plugin also expects a token file. In Bub runtime the default token path is:

```text
<bub-home>/mi_token.json
```

This is wired in [src/bub_xiaoai/plugin.py](/home/frost/workspace/bub-xiaoai/src/bub_xiaoai/plugin.py).

### Example environment

```bash
export BUB_MI_HARDWARE=LX06
export BUB_MI_ACCOUNT='your-xiaomi-account'
export BUB_MI_PASSWORD='your-xiaomi-password'
export BUB_MI_MI_DID='your-device-did'
```

Or with cookie login:

```bash
export BUB_MI_HARDWARE=LX06
export BUB_MI_COOKIE='deviceId=...; serviceToken=...; userId=...'
export BUB_MI_MI_DID='your-device-did'
```

## Bub usage

Once the plugin is loaded by Bub:

- speech coming from XiaoAi arrives on channel `xiaoai`
- the LLM can answer by calling `xiaoai.speak`

The provided skill at [src/skills/xiaoai/SKILL.md](/home/frost/workspace/bub-xiaoai/src/skills/xiaoai/SKILL.md) tells the model to use `xiaoai.speak` for user-facing spoken replies.

## Example flow

1. User talks to XiaoAi.
2. `XiaoAiChannel` polls Xiaomi conversation records.
3. A new record is converted into a Bub message.
4. The assistant handles the message.
5. The assistant calls `xiaoai.speak`.
6. The plugin speaks the response on the speaker.
7. After TTS finishes, the plugin tries to wake XiaoAi again.

Relevant implementation files:

- [src/bub_xiaoai/plugin.py](/home/frost/workspace/bub-xiaoai/src/bub_xiaoai/plugin.py)
- [src/bub_xiaoai/channel.py](/home/frost/workspace/bub-xiaoai/src/bub_xiaoai/channel.py)
- [src/bub_xiaoai/mi.py](/home/frost/workspace/bub-xiaoai/src/bub_xiaoai/mi.py)

## Design notes

- The Xiaomi integration logic is adapted from `xiaogpt`, especially device discovery, conversation polling, and MiIO wake-up commands.
- The code is intentionally scoped to Bub integration instead of reproducing all of `xiaogpt`.
- There is no prompt management, model orchestration, or standalone command runner here.

## Known limitations

- Xiaomi login can fail due to Xiaomi risk control. Cookie login is often more reliable.
- Automatic wake-up depends on MiIO commands and a usable `mi_did`.
- The channel currently ignores the isolated wake word `小爱同学`.
- Only the latest XiaoAi conversation records are polled; this is not a full history sync.

## Development

Run a quick syntax check:

```bash
python3 -m compileall src
```

Run a minimal import check in the project environment:

```bash
uv run python - <<'PY'
from bub_xiaoai import XiaoAiMessageListener, XiaoAiSettings
print(XiaoAiMessageListener, XiaoAiSettings)
PY
```

## Credits

- Core Xiaomi speaker integration ideas in this project come from [`yihong0618/xiaogpt`](https://github.com/yihong0618/xiaogpt) by [@yihong0618](https://github.com/yihong0618), including device discovery, conversation polling, MiIO wake-up control, and the overall continuous-conversation interaction model.
- Built for the Bub plugin/runtime model
