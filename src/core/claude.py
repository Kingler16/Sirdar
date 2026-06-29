"""Claude Code CLI Wrapper für Sirdar.

Ruft Claude im non-interactive Modus (`claude --print`) via subprocess auf und
gibt Analyse + optionalen strukturierten JSON-Block zurück. Portiert aus Velora
(src/analysis/claude.py) — die Mechanik (Binary-Resolution, OAuth-Token-Handling,
fcntl-Filelock, Timeout, Model/Effort, Fehlerklassifikation) ist faithful
übernommen, nur an Sirdars Config (src/config.py) angepasst.

Kostet 0 € API-Gebühren: läuft über das eigene Claude-Abo (CLI), nicht die API.
Muss ohne Token sauber degradieren — wirft eine klare ``ClaudeCLIError`` statt zu
crashen, sodass der App-Start (FastAPI) nie an einer fehlenden Auth scheitert.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from src.config import load_settings

logger = logging.getLogger(__name__)


class ClaudeCLIError(RuntimeError):
    """Claude-CLI-Aufruf fehlgeschlagen (exit code, timeout, auth, leere Antwort).

    Caller MUSS diese Exception fangen — niemals den Fehlertext als Coach-Antwort
    weiterreichen.
    """


# Filelock im Sirdar-Namespace, damit er nicht mit Veloras Lock kollidiert, falls
# beide Projekte auf demselben Host (RockPi) am selben Claude-Konto hängen.
_LOCK_PATH = Path.home() / ".claude" / ".sirdar-cli.lock"

# Defaults, falls settings.claude diese nicht setzt.
_DEFAULT_MODEL = "claude-opus-4-8"
_DEFAULT_EFFORT = "high"


def _resolve_claude_bin() -> str:
    """Claude-Binary-Pfad auflösen: settings.claude.command > PATH > bekannte Pfade."""
    claude_bin = "claude"
    try:
        claude_bin = (load_settings().get("claude", {}) or {}).get("command", "claude") or "claude"
    except Exception:
        pass

    if claude_bin == "claude" or not Path(claude_bin).is_absolute():
        found = shutil.which("claude")
        if found:
            return found
        home = Path.home()
        candidates = [
            home / ".local" / "bin" / "claude",
            home / ".npm-global" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/usr/bin/claude"),
            Path("/snap/bin/claude"),
        ]
        nvm_dir = home / ".nvm" / "versions" / "node"
        if nvm_dir.exists():
            for node_ver in sorted(nvm_dir.iterdir(), reverse=True):
                candidates.append(node_ver / "bin" / "claude")
        for c in candidates:
            if c.exists() and os.access(c, os.X_OK):
                logger.info("Claude CLI gefunden: %s", c)
                return str(c)
        logger.warning("Claude CLI nicht in bekannten Pfaden — versuche 'claude' direkt.")
    return claude_bin


def claude_available() -> bool:
    """True, wenn ein ausführbares ``claude``-Binary gefunden wird.

    Reiner PATH-/Pfad-Check ohne Aufruf — für das Dashboard, das den
    CLI-Status anzeigt, ohne einen (teuren) Roundtrip zu provozieren.
    """
    bin_path = _resolve_claude_bin()
    if Path(bin_path).is_absolute():
        return Path(bin_path).exists() and os.access(bin_path, os.X_OK)
    return shutil.which(bin_path) is not None


def build_claude_env() -> dict:
    """Prozess-Environment für jeden CLI-Aufruf.

    Injiziert den langlebigen OAuth-Token (erzeugt via ``claude setup-token``) als
    ``CLAUDE_CODE_OAUTH_TOKEN``, sofern in settings (``claude.oauth_token``) oder
    der gleichnamigen ENV-Variable hinterlegt. Dieser 1-Jahres-Token ersetzt die
    fragile 8h-Subscription-Auth, die auf einem geteilten Headless-Server (RockPi)
    regelmäßig invalidiert (Velora-Erfahrung).

    Ohne hinterlegten Token (z.B. lokaler Mac mit Keychain) bleibt das Environment
    unverändert und die CLI nutzt ihre Standard-Credentials.
    """
    env = os.environ.copy()
    try:
        token = ((load_settings().get("claude", {}) or {}).get("oauth_token") or "").strip()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except Exception:
        logger.warning("OAuth-Token konnte nicht geladen werden — CLI nutzt Standard-Auth", exc_info=True)
    return env


def _model_and_effort() -> tuple[str, str]:
    """Model + Effort aus settings.claude (mit Defaults)."""
    cfg = load_settings().get("claude", {}) or {}
    return cfg.get("model") or _DEFAULT_MODEL, cfg.get("effort") or _DEFAULT_EFFORT


def ask_claude(system_prompt: str, user_prompt: str, timeout: int = 1200, web_tools: bool = False) -> dict:
    """Ruft die Claude Code CLI auf und gibt Text + optionalen JSON-Block zurück.

    Der Prompt wird via stdin übergeben. Model/Effort kommen aus settings.claude.

    web_tools=True: erlaubt NUR die read-only Web-Tools (WebSearch, WebFetch) —
    z.B. damit der Coach später Routen/Wetter selbst recherchieren kann. Keine
    Edit-/Bash-/Write-Tools.

    Returns:
        {"text": <voller Text>, "structured": <dict|None aus ```json-Block>}

    Raises:
        ClaudeCLIError: Bei exit≠0, leerer Antwort, Timeout, fehlendem Binary oder
            Auth-Fehler (401). Caller MUSS die Exception fangen.
    """
    claude_bin = _resolve_claude_bin()
    model, effort = _model_and_effort()
    cmd = [
        claude_bin,
        "--print",
        "--system-prompt", system_prompt,
        "--no-session-persistence",
        "--model", model,
        "--effort", effort,
    ]
    if web_tools:
        # Read-only Web-Recherche — Claude darf selbst suchen + Seiten öffnen.
        cmd += ["--allowedTools", "WebSearch,WebFetch", "--permission-mode", "acceptEdits"]
    else:
        cmd += ["--tools", ""]

    # File-Lock serialisiert parallele Aufrufe (web + collect + plan). Verhindert
    # Race Conditions beim Token-Refresh, die die .credentials.json korrumpieren.
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        logger.info("Claude CLI wird aufgerufen (Prompt: %d Zeichen)...", len(user_prompt))
        try:
            result = subprocess.run(
                cmd,
                input=user_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=build_claude_env(),
            )
        except subprocess.TimeoutExpired:
            logger.error("Claude CLI Timeout nach %ss", timeout)
            raise ClaudeCLIError(f"Timeout nach {timeout}s")
        except FileNotFoundError:
            logger.error("Claude CLI nicht gefunden. Ist 'claude' im PATH?")
            raise ClaudeCLIError("Claude CLI nicht installiert")
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        combined = (stderr or stdout).lower()
        if "401" in combined or "authentication" in combined or ("invalid" in combined and "credentials" in combined):
            logger.error("Claude CLI Auth-Fehler (exit %d): stderr=%s stdout=%s",
                         result.returncode, stderr[:500], stdout[:500])
            raise ClaudeCLIError(
                "Claude CLI ist nicht mehr authentifiziert (401). "
                "Bitte am Host interaktiv `claude` starten und `/login` (oder `claude setup-token`) ausführen."
            )
        logger.error("Claude CLI Fehler (exit %d): stderr=%r stdout=%r",
                     result.returncode, stderr[:1000], stdout[:1000])
        detail = stderr[:200] or stdout[:200] or "(kein stderr/stdout)"
        raise ClaudeCLIError(f"Claude CLI exit {result.returncode}: {detail}")

    output = (result.stdout or "").strip()
    if not output:
        stderr = (result.stderr or "").strip()
        logger.error("Claude CLI leere Ausgabe. Stderr: %s", stderr[:500])
        raise ClaudeCLIError(f"Leere Antwort von Claude (stderr: {stderr[:200] or 'leer'})")

    logger.info("Claude Antwort: %d Zeichen", len(output))
    return {
        "text": output,
        "structured": extract_json_block(output),
    }


def extract_json_block(text: str) -> dict | None:
    """Extrahiert den letzten ```json-Block aus Claudes Antwort (oder None)."""
    pattern = r"```json\s*\n(.*?)\n\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError as e:
            logger.error("JSON-Parse-Fehler: %s", e)
    return None


def strip_json_block(text: str) -> str:
    """Entfernt einen abschließenden ```json-Block aus dem Text (für die Anzeige)."""
    pattern = r"\n*```json\s*\n.*?\n\s*```\s*$"
    return re.sub(pattern, "", text, flags=re.DOTALL).strip()
