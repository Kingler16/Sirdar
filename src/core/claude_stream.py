"""Claude Code CLI Streaming-Wrapper für den Sirdar Coach-Chat.

Startet die CLI im Non-Interactive-Modus (``--print``) mit ``--output-format
stream-json`` und yielded geparste Events (Text-Deltas, Tool-Calls/-Results,
done). Session-Persistenz via ``--session-id`` / ``--resume``, sodass Folge-
Nachrichten die History nicht erneut im Prompt transportieren müssen.

Grundgerüst portiert aus Velora (src/chat/claude_stream.py). In Phase 0 noch
nicht über die Web-UI verdrahtet — muss aber importierbar sein und für den
Coach-Chat (Phase 2) bereitstehen. Teilt sich den Filelock + die Env-/Binary-
Auflösung mit ``ask_claude`` (src/core/claude.py).
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from src.core.claude import _LOCK_PATH, _resolve_claude_bin, build_claude_env

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """Geparstes Event aus dem stream-json-Output der CLI."""

    type: str
    data: dict


def _extract_tool_use(raw: dict) -> Optional[dict]:
    """Gibt {'id', 'name', 'input'} zurück, falls ein tool_use-Block im Event ist."""
    msg = raw.get("message") if isinstance(raw.get("message"), dict) else raw
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input") or {},
                }
    return None


def _extract_tool_result(raw: dict) -> Optional[dict]:
    """Gibt {'tool_use_id', 'content'} zurück, falls ein tool_result-Block da ist."""
    msg = raw.get("message") if isinstance(raw.get("message"), dict) else raw
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                c = block.get("content")
                if isinstance(c, list):
                    texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                    c = "\n".join(texts)
                return {"tool_use_id": block.get("tool_use_id"), "content": c}
    return None


def _extract_text_blocks(raw: dict) -> list[str]:
    """Alle finalen Text-Blöcke aus einer assistant-message ziehen."""
    out: list[str] = []
    msg = raw.get("message") if isinstance(raw.get("message"), dict) else raw
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    out.append(t)
    return out


async def stream_chat(
    *,
    user_prompt: str,
    system_prompt: str,
    session_id: Optional[str] = None,
    resume_session_id: Optional[str] = None,
    allowed_tools: Optional[list[str]] = None,
    model: str = "claude-opus-4-8",
    effort: str = "high",
    cwd: Optional[str] = None,
) -> AsyncIterator[StreamEvent]:
    """Ruft die Claude CLI auf und yielded geparste Events.

    Yields:
        StreamEvent('text_delta', {'text': ...})
        StreamEvent('assistant_message', {'text': ...})
        StreamEvent('tool_use', {'id', 'name', 'input'})
        StreamEvent('tool_result', {'tool_use_id', 'content'})
        StreamEvent('system', {'session_id', 'model'})
        StreamEvent('done', {'session_id', 'total_cost_usd', 'usage', ...})
        StreamEvent('error', {'message', 'stderr'})
    """
    claude_bin = _resolve_claude_bin()

    cmd: list[str] = [
        claude_bin,
        "--print",
        "--system-prompt", system_prompt,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",  # stream-json benötigt --verbose, sonst Fehler
        "--model", model,
        "--effort", effort,
    ]

    if allowed_tools is not None:
        cmd += ["--allowedTools", ",".join(allowed_tools), "--permission-mode", "dontAsk"]
    else:
        cmd += ["--tools", ""]

    if resume_session_id:
        cmd += ["--resume", resume_session_id]
    elif session_id:
        cmd += ["--session-id", session_id]

    logger.info("Claude CLI stream start: session=%s resume=%s tools=%s",
                session_id, resume_session_id, allowed_tools)

    # Denselben Filelock wie ask_claude nur um den Subprozess-START nehmen: der
    # OAuth-Token-Refresh passiert beim CLI-Start, und ohne Serialisierung
    # korrumpieren parallele Refreshes die .credentials.json. Lock wird SOFORT
    # nach dem Spawn freigegeben — nicht über die Stream-Dauer gehalten.
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=build_claude_env(),
        )
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()

    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(user_prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    stderr_task = asyncio.create_task(proc.stderr.read())
    emitted_text = False  # ob wir Text bereits via deltas gestreamt haben

    try:
        async for line_bytes in proc.stdout:
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Non-JSON line from CLI: %s", line[:200])
                continue

            t = raw.get("type")

            if t == "stream_event":
                ev = raw.get("event") or {}
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            emitted_text = True
                            yield StreamEvent("text_delta", {"text": text})
                continue

            if t == "assistant":
                tool_use = _extract_tool_use(raw)
                if tool_use:
                    yield StreamEvent("tool_use", tool_use)
                # Falls keine deltas kamen, finale Text-Blöcke nachreichen.
                if not emitted_text:
                    for text in _extract_text_blocks(raw):
                        yield StreamEvent("assistant_message", {"text": text})
                continue

            if t == "user":
                tool_result = _extract_tool_result(raw)
                if tool_result:
                    yield StreamEvent("tool_result", tool_result)
                continue

            if t == "result":
                yield StreamEvent("done", {
                    "session_id": raw.get("session_id"),
                    "total_cost_usd": raw.get("total_cost_usd"),
                    "usage": raw.get("usage"),
                    "num_turns": raw.get("num_turns"),
                    "is_error": raw.get("is_error", False),
                })
                continue

            if t == "system":
                yield StreamEvent("system", {
                    "session_id": raw.get("session_id"),
                    "model": raw.get("model"),
                })
                continue

            yield StreamEvent("raw", raw)

    finally:
        rc = await proc.wait()
        stderr = (await stderr_task).decode("utf-8", errors="replace")
        if rc != 0:
            logger.error("Claude CLI exit %d. stderr: %s", rc, stderr[:2000])
            yield StreamEvent("error", {
                "message": f"Claude CLI Exit-Code {rc}",
                "stderr": stderr[-2000:],
            })
        elif stderr.strip():
            logger.debug("Claude stderr (exit 0): %s", stderr[:1000])
