---
name: xiaoai
description: Use this skill when interacting through the XiaoAi channel or controlling XiaoAi-connected devices.
---

# XiaoAi

Use this skill when the active interaction happens through XiaoAi, including spoken replies and XiaoAi-executed commands.

## Rule

If the assistant is delivering a user-facing reply through XiaoAi, call `xiaoai_speak`.

If the assistant needs XiaoAi to execute an instruction, call `xiaoai_exec`.

If the assistant wants to continue the conversation after replying, it must call `xiaoai_listen` to fetch a new XiaoAi message before continuing. Do not assume a follow-up message exists unless `xiaoai_listen` returns one.

## How to speak

- Pass the exact final spoken reply as the `text` argument.
- Keep the spoken text concise and natural for TTS.
- Make one `xiaoai_speak` call per reply unless the task explicitly needs multiple utterances.
- Do not call `xiaoai_speak` for hidden reasoning, tool chatter, or non-user-facing status messages.

## Sending XiaoAi commands

Use `xiaoai_exec` when the task is to send a device command to XiaoAi instead of speaking a reply.

- Pass the XiaoAi instruction as the `command` argument.
- Use natural commands that XiaoAi can execute directly, such as turning lights on or off.
- Do not wrap the command in explanatory text.
- If the user asks to operate a device, prefer `xiaoai_exec` over `xiaoai_speak`.

Example:

`xiaoai_exec command=关灯`

## Continuing the conversation

- After speaking, if you intend to keep talking with the user, call `xiaoai_listen` to get the next XiaoAi message.
- Only continue the dialog when `xiaoai_listen` returns a new user message.
- If `xiaoai_listen` does not return a new message, end the turn instead of inventing a continuation.

## Default pattern

1. Finish any required reasoning or tool work.
2. Prepare the final reply text for the user.
3. Call `xiaoai_speak(text=<final reply>)`.
4. If you want to continue the conversation, call `xiaoai_listen()` and wait for a new message before proceeding.
5. If a text response is also required, keep it aligned with what was spoken.
