#!/usr/bin/env python3
"""Daily 1Point3Acres automation through a dedicated logged-in Chrome profile.

This runner does not solve or bypass human verification. It operates only on a
normal, already logged-in Chrome page through local browser automation.
"""

import argparse
import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
PUBLIC_BANK_FILE = DATA_DIR / "question_bank.json"
LOCAL_BANK_FILE = DATA_DIR / "local_question_bank.json"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env")

CHROME_APP_NAME = os.environ.get("CHROME_APP_NAME", "Google Chrome").strip() or "Google Chrome"
CHROME_EXECUTABLE = Path(
    os.environ.get(
        "CHROME_EXECUTABLE",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
)
CHROME_PROFILE_DIRECTORY = os.environ.get("CHROME_PROFILE_DIRECTORY", "Default").strip()
CHROME_CONTROL_MODE = os.environ.get("CHROME_CONTROL_MODE", "cdp").strip() or "cdp"
CHROME_CDP_ADDRESS = os.environ.get("CHROME_CDP_ADDRESS", "127.0.0.1").strip() or "127.0.0.1"
CHROME_CDP_PORT = int(os.environ.get("CHROME_CDP_PORT", "9223"))
CHROME_USER_DATA_DIR = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "CHROME_USER_DATA_DIR",
                "~/Library/Application Support/daily_checkin/chrome-profile",
            )
        )
    )
)
CHROME_EXTENSION_TIMEOUT = int(os.environ.get("CHROME_EXTENSION_TIMEOUT", "0"))
CLICK_WAIT_MIN_SECONDS = float(os.environ.get("CLICK_WAIT_MIN_SECONDS", "0.8"))
CLICK_WAIT_MAX_SECONDS = float(os.environ.get("CLICK_WAIT_MAX_SECONDS", "2.4"))
LAUNCH_RANDOM_DELAY_MIN_SECONDS = float(os.environ.get("LAUNCH_RANDOM_DELAY_MIN_SECONDS", "0"))
LAUNCH_RANDOM_DELAY_MAX_SECONDS = float(os.environ.get("LAUNCH_RANDOM_DELAY_MAX_SECONDS", "600"))
ACTIVE_URL_PREFIX = ""
CDP_CONTROLLER = None

QUESTION_URL = "https://www.1point3acres.com/next/daily-question"
CHECKIN_URL = "https://www.1point3acres.com/next/daily-checkin"
QUESTION_SUBMIT_SPAN_SELECTOR = (
    "#__next > div.from-background-home.to-background-home-secondary.flex.min-h-screen.flex-col."
    "bg-gradient-to-r.from-50\\%.to-50\\%.text-sm.overflow-visible > main > div > div > "
    "main > div.min-h-\\[40vh\\].rounded-md.bg-white.p-5 > div > div.mt-1\\.5 > "
    "div > div.mt-2\\.5.text-center > button > span"
)
CHECKIN_SUBMIT_SPAN_SELECTOR = (
    "#__next > div.from-background-home.to-background-home-secondary.flex.min-h-screen.flex-col."
    "bg-gradient-to-r.from-50\\%.to-50\\%.text-sm.overflow-visible > main > div > div > "
    "main > div.min-h-\\[40vh\\].rounded-md.bg-white.p-5 > div > div.mt-2\\.5 > "
    "div:nth-child(2) > div > button > span"
)

CHECKIN_MESSAGES = [
    "Today is a good day.",
    "Checking in for the day.",
    "Keep going.",
]

# These text checks are fallbacks only. The primary signals below come from
# stable page structure: routes, forms, password inputs, captcha widgets, and
# the presence or absence of actionable daily-task controls.
MANUAL_ATTENTION_FALLBACK_PATTERNS = (
    "请先登录",
    "登录后进行签到",
    "登录后进行答题",
    "真人验证",
    "人机验证",
    "验证码",
    "captcha",
)

QUESTION_DONE_FALLBACK_PATTERNS = (
    "今日已答题",
    "已答题",
    "已完成今日答题",
    "已经答过",
    "今日答题已完成",
    "明天再来",
    "答题成功",
    "恭喜你答题成功",
)

QUESTION_FAILED_FALLBACK_PATTERNS = (
    "答题失败",
    "抱歉，你答题失败",
)

CHECKIN_DONE_FALLBACK_PATTERNS = (
    "今日已签到",
    "今天已签到",
    "签到成功",
)


class DailyError(RuntimeError):
    pass


class ManualAttentionError(DailyError):
    pass


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def click_wait_range() -> Tuple[float, float]:
    lower = max(0.0, CLICK_WAIT_MIN_SECONDS)
    upper = max(0.0, CLICK_WAIT_MAX_SECONDS)
    if upper < lower:
        lower, upper = upper, lower
    return lower, upper


def wait_random_range(action: str, lower: float, upper: float) -> None:
    lower = max(0.0, lower)
    upper = max(0.0, upper)
    if upper < lower:
        lower, upper = upper, lower
    if upper <= 0:
        return
    delay = random.uniform(lower, upper)
    log(f"Waiting {delay:.2f}s before {action}.")
    time.sleep(delay)


def wait_before_click(action: str) -> None:
    lower, upper = click_wait_range()
    wait_random_range(action, lower, upper)


def wait_before_launch_start() -> None:
    wait_random_range(
        "starting LaunchAgent daily run",
        LAUNCH_RANDOM_DELAY_MIN_SECONDS,
        LAUNCH_RANDOM_DELAY_MAX_SECONDS,
    )


def http_json(path: str, method: str = "GET", timeout: float = 2.0) -> Dict[str, Any]:
    conn = http.client.HTTPConnection(CHROME_CDP_ADDRESS, CHROME_CDP_PORT, timeout=timeout)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise DailyError(f"CDP HTTP {method} {path} failed: {response.status} {body[:300]}")
        return json.loads(body)
    finally:
        conn.close()


def http_text(path: str, method: str = "GET", timeout: float = 2.0) -> str:
    conn = http.client.HTTPConnection(CHROME_CDP_ADDRESS, CHROME_CDP_PORT, timeout=timeout)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise DailyError(f"CDP HTTP {method} {path} failed: {response.status} {body[:300]}")
        return body
    finally:
        conn.close()


class CdpWebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws":
            raise DailyError(f"Unsupported CDP websocket URL: {url}")
        self.host = parsed.hostname or CHROME_CDP_ADDRESS
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.next_id = 1
        self._handshake()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._recv_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise DailyError(f"CDP websocket handshake failed: {response[:300]!r}")
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if accept.encode("ascii") not in response:
            raise DailyError("CDP websocket handshake returned an invalid accept key")

    def _recv_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _recv_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise DailyError("CDP websocket closed unexpectedly")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126, (length >> 8) & 0xFF, length & 0xFF])
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_message(self) -> Dict[str, Any]:
        payload_parts = []
        while True:
            first, second = self._recv_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._recv_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._recv_exact(8), "big")
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 8:
                raise DailyError("CDP websocket closed")
            if opcode == 9:
                self._send_frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode in {0, 1}:
                payload_parts.append(payload)
                if fin:
                    return json.loads(b"".join(payload_parts).decode("utf-8"))

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        previous_timeout = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            self._send_frame(1, json.dumps(message, separators=(",", ":")).encode("utf-8"))
            while True:
                response = self._recv_message()
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise DailyError(f"CDP {method} failed: {response['error']}")
                return response.get("result", {})
        finally:
            self.sock.settimeout(previous_timeout)


class CdpChromeController:
    def __init__(self) -> None:
        self.ws: Optional[CdpWebSocket] = None
        self.target_id: Optional[str] = None
        self.opened_target_ids: List[str] = []

    def _remember_opened_target(self, target: Dict[str, Any]) -> None:
        target_id = str(target.get("id") or "")
        if target_id and target_id not in self.opened_target_ids:
            self.opened_target_ids.append(target_id)

    def _chrome_args(self, url: Optional[str] = None) -> List[str]:
        args = [
            str(CHROME_EXECUTABLE),
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            f"--profile-directory={CHROME_PROFILE_DIRECTORY or 'Default'}",
            f"--remote-debugging-address={CHROME_CDP_ADDRESS}",
            f"--remote-debugging-port={CHROME_CDP_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if url:
            args.append(url)
        return args

    def _launch_chrome(self, url: Optional[str] = None) -> None:
        if not CHROME_EXECUTABLE.exists():
            raise DailyError(f"Chrome executable not found: {CHROME_EXECUTABLE}")
        CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            self._chrome_args(url),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def ensure_running(self, start_url: Optional[str] = None) -> Dict[str, Any]:
        try:
            return http_json("/json/version", timeout=1.0)
        except Exception:
            pass

        self._launch_chrome(start_url)

        deadline = time.time() + 20
        last_error = ""
        while time.time() < deadline:
            try:
                return http_json("/json/version", timeout=1.0)
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.5)
        raise DailyError(
            f"CDP endpoint did not become available at {CHROME_CDP_ADDRESS}:{CHROME_CDP_PORT} "
            f"({last_error})"
        )

    def _page_targets(self) -> List[Dict[str, Any]]:
        targets = http_json("/json", timeout=5.0)
        if not isinstance(targets, list):
            raise DailyError(f"Unexpected CDP target list: {targets}")
        return [target for target in targets if target.get("type") == "page"]

    def _attach_target(self, target: Dict[str, Any]) -> None:
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise DailyError(f"CDP target did not expose a websocket URL: {target}")
        if self.ws:
            self.ws.close()
        self.target_id = target.get("id")
        self.ws = CdpWebSocket(ws_url)
        self.ws.request("Page.enable")
        self.ws.request("Runtime.enable")

    def _open_profile_target(self, url: str) -> None:
        try:
            existing_target_ids = {str(target.get("id") or "") for target in self._page_targets()}
        except Exception:
            existing_target_ids = set()
        self._launch_chrome(url)
        self.ensure_running()
        deadline = time.time() + 20
        last_seen = ""
        fallback_target: Optional[Dict[str, Any]] = None
        while time.time() < deadline:
            for target in self._page_targets():
                target_url = str(target.get("url") or "")
                target_id = str(target.get("id") or "")
                last_seen = target_url or last_seen
                if target_url.startswith(url):
                    if not fallback_target:
                        fallback_target = target
                    if target_id in existing_target_ids:
                        continue
                    self._attach_target(target)
                    self._remember_opened_target(target)
                    return
            time.sleep(0.5)
        if fallback_target:
            self._attach_target(fallback_target)
            return
        raise DailyError(
            f"Chrome did not open {url} in profile {CHROME_PROFILE_DIRECTORY or 'Default'} "
            f"(last page URL: {last_seen or 'none'})"
        )

    def _create_target(self, url: str) -> None:
        self.ensure_running()
        encoded_url = urllib.parse.quote(url, safe="")
        target = http_json(f"/json/new?{encoded_url}", method="PUT", timeout=5.0)
        self._attach_target(target)
        self._remember_opened_target(target)

    def open(self, url: str, new_tab: bool = False) -> None:
        if new_tab:
            self._create_target(url)
            return
        if not self.ws:
            self._open_profile_target(url)
            return
        self.ws.request("Page.navigate", {"url": url})

    def close_opened_targets(self) -> int:
        closed = 0
        for target_id in list(reversed(self.opened_target_ids)):
            try:
                http_text(f"/json/close/{urllib.parse.quote(target_id, safe='')}", timeout=3.0)
                closed += 1
            except Exception as exc:
                log(f"Could not close Chrome tab {target_id}: {exc}")
        self.opened_target_ids.clear()
        if self.ws:
            self.ws.close()
            self.ws = None
            self.target_id = None
        return closed

    def eval(self, js: str) -> str:
        if not self.ws:
            self._create_target("about:blank")
        assert self.ws is not None
        result = self.ws.request(
            "Runtime.evaluate",
            {
                "expression": js,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("text") or details.get("exception", {}).get("description") or details
            raise DailyError(f"JavaScript evaluation failed: {text}")
        remote = result.get("result", {})
        if "value" in remote:
            return str(remote["value"])
        if remote.get("type") == "undefined":
            return ""
        return str(remote.get("unserializableValue", ""))


def cdp_controller() -> CdpChromeController:
    global CDP_CONTROLLER
    if CDP_CONTROLLER is None:
        CDP_CONTROLLER = CdpChromeController()
    return CDP_CONTROLLER


def set_control_mode(mode: str) -> None:
    global CHROME_CONTROL_MODE
    CHROME_CONTROL_MODE = mode


def run_osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise DailyError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def chrome_target_script(js_path: str) -> str:
    return (
        f"set jsCode to read POSIX file {applescript_string(js_path)}\n"
        f"set urlPrefix to {applescript_string(ACTIVE_URL_PREFIX)}\n"
        f"tell application {applescript_string(CHROME_APP_NAME)}\n"
        "  if not (exists window 1) then\n"
        "    if urlPrefix is \"\" then\n"
        "      make new window\n"
        "    else\n"
        "      error \"Chrome target tab is not available yet\"\n"
        "    end if\n"
        "  end if\n"
        "  set targetWindow to missing value\n"
        "  set targetTabIndex to missing value\n"
        "  if urlPrefix is not \"\" then\n"
        "    repeat with w in windows\n"
        "      repeat with tabNumber from 1 to count tabs of w\n"
        "        set t to tab tabNumber of w\n"
        "        try\n"
        "          if (URL of t starts with urlPrefix) then\n"
        "            set targetWindow to w\n"
        "            set targetTabIndex to tabNumber\n"
        "            exit repeat\n"
        "          end if\n"
        "        end try\n"
        "      end repeat\n"
        "      if targetWindow is not missing value then exit repeat\n"
        "    end repeat\n"
        "  end if\n"
        "  if targetWindow is missing value then\n"
        "    if urlPrefix is not \"\" then\n"
        "      error \"Chrome target tab is not available yet\"\n"
        "    else\n"
        "      set targetWindow to window 1\n"
        "      set targetTabIndex to active tab index of window 1\n"
        "    end if\n"
        "  else\n"
        "    set active tab index of targetWindow to targetTabIndex\n"
        "  end if\n"
        "  execute active tab of targetWindow javascript jsCode\n"
        "end tell"
    )


def chrome_launch(url: str) -> None:
    if not CHROME_EXECUTABLE.exists():
        raise DailyError(f"Chrome executable not found: {CHROME_EXECUTABLE}")

    args = [str(CHROME_EXECUTABLE)]
    if CHROME_PROFILE_DIRECTORY:
        args.append(f"--profile-directory={CHROME_PROFILE_DIRECTORY}")
    args.append(url)
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1)


def chrome_eval(js: str) -> str:
    if CHROME_CONTROL_MODE == "cdp":
        return cdp_controller().eval(js)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as temp:
        temp.write(js)
        temp_path = temp.name
    try:
        script = chrome_target_script(temp_path)
        for attempt in range(3):
            try:
                return run_osascript(script)
            except DailyError as exc:
                err_msg = str(exc)
                if attempt < 2 and any(t in err_msg for t in ("timed out", "-1712", "timeout")):
                    log(f"AppleScript timed out (attempt {attempt+1}/3), retrying in 2s...")
                    time.sleep(2)
                    continue
                raise
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def chrome_open(url: str, new_tab: bool = False) -> None:
    if CHROME_CONTROL_MODE == "cdp":
        cdp_controller().open(url, new_tab=new_tab)
        return

    global ACTIVE_URL_PREFIX
    ACTIVE_URL_PREFIX = url
    chrome_launch(url)


def wait_for_url(url: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            current = chrome_eval("location.href")
        except DailyError as exc:
            last_error = str(exc)
            if "Chrome target tab is not available yet" in last_error:
                time.sleep(0.5)
                continue
            raise
        if current.startswith(url):
            return
        time.sleep(0.5)
    if last_error:
        raise DailyError(f"Chrome did not navigate to {url} ({last_error})")
    raise DailyError(f"Chrome did not navigate to {url}")


def wait_for_ready(timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = chrome_eval("document.readyState")
        if state in {"interactive", "complete"}:
            return
        time.sleep(0.5)
    raise DailyError("Page did not finish loading")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"^【?\s*题目\s*】?", "", value)
    value = value.lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[\"'`‘’“”·,，.。:：;；!！?？()\[\]{}<>《》、/\\|-]", "", value)
    return value


def clean_question(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"^【?\s*题目\s*】?\s*", "", value).strip()


def contains_any(text: str, patterns: Iterable[str]) -> bool:
    return bool(matching_patterns(text, patterns))


def matching_patterns(text: str, patterns: Iterable[str]) -> List[str]:
    compact = normalize(text)
    return [pattern for pattern in patterns if normalize(pattern) in compact]


def page_body(state: Dict[str, Any]) -> str:
    return str(state.get("bodyText", "") or "")


def dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def evidence_summary(values: Iterable[str]) -> str:
    evidence = dedupe(str(value) for value in values if str(value).strip())
    if not evidence:
        return "no evidence"
    if len(evidence) <= 4:
        return ", ".join(evidence)
    return ", ".join(evidence[:4]) + f", +{len(evidence) - 4} more"


def state_summary(state: Dict[str, Any]) -> str:
    fields = [
        "url",
        "title",
        "question",
        "answerOptionCount",
        "hasQuestionSubmit",
        "submitDisabled",
        "hasTextarea",
        "textareaVisible",
        "hasCheckinSubmit",
    ]
    values = []
    for field in fields:
        if field in state:
            values.append(f"{field}={state.get(field)!r}")
    reasons = manual_attention_reasons(state)
    if reasons:
        values.append(f"manualAttention={evidence_summary(reasons)!r}")
    return evidence_summary(values)


def manual_attention_reasons(state: Dict[str, Any]) -> List[str]:
    attention = state.get("attention") if isinstance(state, dict) else {}
    reasons = list((attention or {}).get("evidence") or [])
    for pattern in matching_patterns(page_body(state), MANUAL_ATTENTION_FALLBACK_PATTERNS):
        reasons.append(f'text fallback "{pattern}"')
    return dedupe(reasons)


def raise_if_manual_attention_required(state: Dict[str, Any], context: str) -> None:
    reasons = manual_attention_reasons(state)
    if reasons:
        raise ManualAttentionError(
            f"{context} requires manual login or verification ({evidence_summary(reasons)})."
        )


def question_form_is_actionable(state: Dict[str, Any]) -> bool:
    if state.get("question") and state.get("options"):
        return True
    if state.get("hasQuestionSubmit") or int(state.get("answerOptionCount") or 0):
        return True
    return False


def question_text_indicates_done(text: str) -> bool:
    return contains_any(text, QUESTION_DONE_FALLBACK_PATTERNS)


def question_text_indicates_failed(text: str) -> bool:
    return contains_any(text, QUESTION_FAILED_FALLBACK_PATTERNS)


def question_already_done(state: Dict[str, Any]) -> bool:
    if question_text_indicates_done(page_body(state)):
        return True
    if question_form_is_actionable(state):
        return False
    return False


def question_submit_status(state: Dict[str, Any]) -> str:
    body = page_body(state)
    if question_text_indicates_failed(body):
        return "failed"
    if question_already_done(state) or question_text_indicates_done(body):
        return "success"
    return "submitted"


def today_dates() -> Tuple[str, str]:
    today = dt.datetime.now().date()
    return today.isoformat(), f"{today.month}月{today.day}日"


def checkin_text_indicates_done(text: str) -> bool:
    if contains_any(text, CHECKIN_DONE_FALLBACK_PATTERNS):
        return True

    match = re.search(r"上次签到时间[：:\s]+(\d{4}-\d{1,2}-\d{1,2})", text)
    if match:
        iso_today, _ = today_dates()
        return match.group(1) == iso_today
    return False


def checkin_already_done(state: Dict[str, Any]) -> bool:
    return checkin_text_indicates_done(page_body(state))


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def bank_records(path: Path) -> List[Dict[str, Any]]:
    data = load_json(path, [])
    if isinstance(data, dict):
        return list(data.values())
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def answer_values(record: Dict[str, Any]) -> List[str]:
    answers = record.get("answers", record.get("answer", []))
    if isinstance(answers, str):
        return [answers]
    if isinstance(answers, list):
        return [str(item) for item in answers if str(item).strip()]
    return []


def match_answer_to_option(answers: List[str], options: List[str]) -> Optional[int]:
    normalized_options = [normalize(option) for option in options]
    for answer in answers:
        answer_norm = normalize(answer)
        if not answer_norm:
            continue
        for idx, option_norm in enumerate(normalized_options):
            if answer_norm == option_norm:
                return idx
        for idx, option_norm in enumerate(normalized_options):
            if answer_norm in option_norm or option_norm in answer_norm:
                return idx
    return None


def find_bank_answer(question: str, options: List[str]) -> Optional[Dict[str, Any]]:
    question_norm = normalize(question)
    candidates: List[Tuple[str, List[Dict[str, Any]]]] = [
        ("local", bank_records(LOCAL_BANK_FILE)),
        ("public", bank_records(PUBLIC_BANK_FILE)),
    ]

    for source_name, records in candidates:
        for record in records:
            if normalize(str(record.get("question", ""))) != question_norm:
                continue
            idx = match_answer_to_option(answer_values(record), options)
            if idx is not None:
                return {
                    "index": idx,
                    "answer": options[idx],
                    "source": source_name,
                    "record": record,
                }
    return None


PAGE_HELPERS_JS = r"""
  function isVisible(el) {
    if (!el || !el.ownerDocument || !el.ownerDocument.defaultView) return false;
    if (el.tagName === "INPUT" && (el.getAttribute("type") || "").toLowerCase() === "hidden") return false;
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity) === 0) return false;
    if (el.getClientRects().length > 0) return true;
    return ["TEXTAREA", "SELECT"].includes(el.tagName);
  }

  function visibleElements(selector) {
    try {
      return Array.from(document.querySelectorAll(selector)).filter(isVisible);
    } catch (_) {
      return [];
    }
  }

  function cleanText(value) {
    return (value || "").replace(/\s+/g, " ").trim();
  }

  function cleanButtonText(button) {
    const clone = button.cloneNode(true);
    clone.querySelectorAll("img, svg").forEach(el => el.remove());
    return cleanText(clone.textContent || "");
  }

  function elementHaystack(el) {
    const attrs = ["id", "class", "name", "type", "action", "src", "title", "aria-label"];
    return attrs.map(name => el.getAttribute && el.getAttribute(name) || "").join(" ").toLowerCase();
  }

  function collectManualAttention() {
    const evidence = [];
    const url = location.href;
    if (/\/(login|signin)(\/|$)|member\.php\?mod=logging|account\/login/i.test(url)) {
      evidence.push("login route");
    }

    const passwordInputs = visibleElements('input[type="password"]');
    if (passwordInputs.length) {
      evidence.push(`${passwordInputs.length} visible password input(s)`);
    }

    const loginForms = visibleElements("form")
      .filter(form => form.querySelector('input[type="password"]') ||
        /login|signin|logging/.test(elementHaystack(form)));
    if (loginForms.length) {
      evidence.push(`${loginForms.length} login form(s)`);
    }

    const challengeElements = Array.from(document.querySelectorAll("iframe, input, div, section, form"))
      .filter(el => /(captcha|recaptcha|hcaptcha|turnstile|cf-challenge|cloudflare)/i.test(elementHaystack(el)))
      .filter(el => isVisible(el));
    if (challengeElements.length) {
      evidence.push(`${challengeElements.length} visible captcha/challenge element(s)`);
    }

    return { required: evidence.length > 0, evidence };
  }
"""


QUESTION_EXTRACT_JS = r"""
(() => {
  PAGE_HELPERS
  const submitSelector = "SUBMIT_SELECTOR";
  const visibleText = document.body ? document.body.innerText : "";
  const questionNode = document.querySelector(".text-base.text-orange");
  let question = questionNode ? questionNode.innerText.trim() : null;
  if (question) question = question.replace(/^【题目】\s*/, "").trim();
  if (!question) {
    const match = visibleText.match(/【题目】([^\n]+)/);
    question = match ? match[1].trim() : null;
  }

  const buttons = Array.from(document.querySelectorAll("button"));
  const buttonItems = buttons
    .map((button, domIndex) => ({ button, domIndex, text: cleanButtonText(button) }));
  const submitSpan = document.querySelector(submitSelector) || Array.from(document.querySelectorAll("span"))
    .find(span => (span.textContent || "").replace(/\s+/g, "") === "提交答案");
  const submit = submitSpan
    ? submitSpan.closest("button")
    : buttons.find(button => cleanButtonText(button).replace(/\s+/g, "").includes("提交答案"));
  const strictAnswerItems = buttonItems
    .filter(item =>
      item.text &&
      item.button.className.includes("cursor-pointer") &&
      item.button.className.includes("rounded-md") &&
      item.button.className.includes("text-left") &&
      !item.text.includes("提交答案")
    );
  const fallbackAnswerItems = !strictAnswerItems.length && questionNode
    ? buttonItems.filter(item => {
      const compactText = item.text.replace(/\s+/g, "");
      if (!item.text || item.button === submit || compactText.includes("提交答案")) return false;
      if (item.button.closest("header, nav, footer")) return false;
      const afterQuestion = !!(questionNode.compareDocumentPosition(item.button) & Node.DOCUMENT_POSITION_FOLLOWING);
      const beforeSubmit = !submit || !!(item.button.compareDocumentPosition(submit) & Node.DOCUMENT_POSITION_FOLLOWING);
      return afterQuestion && beforeSubmit;
    })
    : [];
  const answerButtons = (strictAnswerItems.length ? strictAnswerItems : fallbackAnswerItems)
    .map((item, index) => {
      const img = item.button.querySelector("img");
      const imgSrc = img ? img.getAttribute("src") || "" : "";
      let extensionMarker = null;
      if (imgSrc.includes("check_right")) extensionMarker = "right";
      else if (imgSrc.includes("check_error")) extensionMarker = "wrong";
      else if (imgSrc.includes("faq")) extensionMarker = "unknown";
      else if (imgSrc.includes("loading")) extensionMarker = "loading";
      return {
        index,
        domIndex: item.domIndex,
        text: item.text,
        className: item.button.className,
        imgSrc,
        extensionMarker
      };
    });
  return JSON.stringify({
    url: location.href,
    title: document.title,
    bodyText: visibleText.slice(0, 5000),
    attention: collectManualAttention(),
    question,
    options: answerButtons,
    answerOptionCount: answerButtons.length,
    hasQuestionSubmit: !!submit,
    submitDisabled: submit ? submit.disabled : null,
  });
})()
"""


QUESTION_CLICK_JS = r"""
(({ index }) => {
  const cleanButtonText = button => (button.textContent || "").replace(/\s+/g, " ").trim();
  const fire = (el, type) => el.dispatchEvent(new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    view: window,
  }));
  const questionNode = document.querySelector(".text-base.text-orange");
  const buttons = Array.from(document.querySelectorAll("button"));
  const submitButton = Array.from(document.querySelectorAll("button"))
    .find(button => cleanButtonText(button).replace(/\s+/g, "").includes("提交答案"));
  const strictAnswerButtons = buttons
    .filter(button =>
      cleanButtonText(button) &&
      button.className.includes("cursor-pointer") &&
      button.className.includes("rounded-md") &&
      button.className.includes("text-left") &&
      !cleanButtonText(button).includes("提交答案")
    );
  const fallbackAnswerButtons = !strictAnswerButtons.length && questionNode
    ? buttons.filter(button => {
      const text = cleanButtonText(button);
      const compactText = text.replace(/\s+/g, "");
      if (!text || button === submitButton || compactText.includes("提交答案")) return false;
      if (button.closest("header, nav, footer")) return false;
      const afterQuestion = !!(questionNode.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING);
      const beforeSubmit = !submitButton || !!(button.compareDocumentPosition(submitButton) & Node.DOCUMENT_POSITION_FOLLOWING);
      return afterQuestion && beforeSubmit;
    })
    : [];
  const answerButtons = strictAnswerButtons.length ? strictAnswerButtons : fallbackAnswerButtons;
  if (!answerButtons[index]) return JSON.stringify({ ok: false, reason: "answer button not found" });
  const answer = answerButtons[index];
  answer.scrollIntoView({ block: "center", inline: "center" });
  answer.focus();
  fire(answer, "pointerdown");
  fire(answer, "mousedown");
  fire(answer, "mouseup");
  fire(answer, "pointerup");
  fire(answer, "click");
  answer.click();
  return JSON.stringify({ ok: true });
})(ARGUMENTS)
"""


SUBMIT_CLICK_JS = r"""
(() => {
  const submitSelector = "SUBMIT_SELECTOR";
  const cleanButtonText = button => (button.textContent || "").replace(/\s+/g, " ").trim();
  const submitSpan = document.querySelector(submitSelector) || Array.from(document.querySelectorAll("span"))
    .find(span => (span.textContent || "").replace(/\s+/g, "") === "提交答案");
  const submitButton = submitSpan
    ? submitSpan.closest("button")
    : Array.from(document.querySelectorAll("button"))
      .find(button => cleanButtonText(button).replace(/\s+/g, "").includes("提交答案"));
  if (!submitButton) return JSON.stringify({ ok: false, reason: "submit button not found" });
  if (submitButton.disabled) return JSON.stringify({ ok: false, reason: "submit button still disabled" });
  submitButton.scrollIntoView({ block: "center", inline: "center" });
  submitButton.focus();
  submitButton.click();
  return JSON.stringify({ ok: true });
})()
"""


CHECKIN_JS = r"""
(({ message, submit }) => {
  const submitSelector = "SUBMIT_SELECTOR";
  const textarea = document.querySelector('textarea[name="todaysay"]') || document.querySelector("textarea");
  if (!textarea) return JSON.stringify({ ok: false, reason: "textarea not found" });
  const submitSpan = document.querySelector(submitSelector) || Array.from(document.querySelectorAll("span"))
    .find(span => (span.textContent || "").replace(/\s+/g, "") === "提交签到");
  const button = submitSpan
    ? submitSpan.closest("button")
    : Array.from(document.querySelectorAll("button"))
      .find(item => (item.textContent || "").replace(/\s+/g, "") === "提交签到");
  if (!button) return JSON.stringify({ ok: false, reason: "submit button not found" });
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
  setter.call(textarea, message);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.dispatchEvent(new Event("change", { bubbles: true }));
  if (submit) {
    button.click();
  }
  return JSON.stringify({ ok: true, message, submitDisabled: button.disabled });
})(ARGUMENTS)
"""


CHECKIN_STATE_JS = r"""
(() => {
  PAGE_HELPERS
  const submitSelector = "SUBMIT_SELECTOR";
  const visibleText = document.body ? document.body.innerText : "";
  const textarea = document.querySelector('textarea[name="todaysay"]') || document.querySelector("textarea");
  const submitSpan = document.querySelector(submitSelector) || Array.from(document.querySelectorAll("span"))
    .find(span => (span.textContent || "").replace(/\s+/g, "") === "提交签到");
  const submitButton = submitSpan
    ? submitSpan.closest("button")
    : Array.from(document.querySelectorAll("button"))
      .find(button => cleanButtonText(button).replace(/\s+/g, "") === "提交签到");
  return JSON.stringify({
    url: location.href,
    title: document.title,
    bodyText: visibleText.slice(0, 5000),
    attention: collectManualAttention(),
    hasTextarea: !!textarea,
    textareaVisible: textarea ? isVisible(textarea) : false,
    hasCheckinSubmit: !!submitButton,
    submitDisabled: submitButton ? submitButton.disabled : null,
  });
})()
"""


def page_state() -> Dict[str, Any]:
    js = (
        QUESTION_EXTRACT_JS
        .replace("PAGE_HELPERS", PAGE_HELPERS_JS)
        .replace('"SUBMIT_SELECTOR"', json.dumps(QUESTION_SUBMIT_SPAN_SELECTOR))
    )
    return json.loads(chrome_eval(js))


def checkin_state() -> Dict[str, Any]:
    js = (
        CHECKIN_STATE_JS
        .replace("PAGE_HELPERS", PAGE_HELPERS_JS)
        .replace('"SUBMIT_SELECTOR"', json.dumps(CHECKIN_SUBMIT_SPAN_SELECTOR))
    )
    return json.loads(chrome_eval(js))


def wait_for_question(timeout: int = 30) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last_state: Dict[str, Any] = {}
    while time.time() < deadline:
        state = page_state()
        last_state = state
        raise_if_manual_attention_required(state, "Question page")
        if question_already_done(state):
            state["alreadyDone"] = True
            return state
        if state.get("question") and state.get("options"):
            state["question"] = clean_question(state["question"])
            return state
        time.sleep(1)
    raise DailyError(f"Could not parse daily question ({state_summary(last_state)}).")


def extension_answer(state: Dict[str, Any], timeout: int) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout
    current = state
    while time.time() < deadline:
        raise_if_manual_attention_required(current, "Question page")
        options = current.get("options", [])
        for option in options:
            if option.get("extensionMarker") == "right":
                return {
                    "index": option["index"],
                    "answer": option["text"],
                    "source": "extension",
                }
        if options and not any(option.get("extensionMarker") == "loading" for option in options):
            return None
        time.sleep(1)
        current = page_state()
    return None


def wait_for_question_submit_status(timeout: int = 8) -> str:
    deadline = time.time() + timeout
    last_status = "submitted"
    last_state: Dict[str, Any] = {}
    while time.time() < deadline:
        state = page_state()
        last_state = state
        status = question_submit_status(state)
        if status != "submitted":
            return status
        raise_if_manual_attention_required(state, "Question page after submit")
        last_status = status
        time.sleep(1)
    log(f"Question submit still pending ({state_summary(last_state)}).")
    return last_status


def wait_for_checkin_submit_status(timeout: int = 8) -> str:
    deadline = time.time() + timeout
    last_state: Dict[str, Any] = {}
    while time.time() < deadline:
        state = checkin_state()
        last_state = state
        if checkin_already_done(state):
            return "success"
        raise_if_manual_attention_required(state, "Check-in page after submit")
        time.sleep(1)
    log(f"Check-in submit still pending ({state_summary(last_state)}).")
    return "submitted"


def remember_answer(
    question: str,
    options: List[str],
    answer: str,
    source: str,
    status: str,
    confirm_answer: bool = False,
) -> None:
    records = bank_records(LOCAL_BANK_FILE)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    question_norm = normalize(question)
    existing = None
    for record in records:
        if normalize(str(record.get("question", ""))) == question_norm:
            existing = record
            break
    if not existing:
        existing = {"question": question, "answers": [], "options_seen": []}
        records.append(existing)
    answers = answer_values(existing)
    if confirm_answer and answer.strip() and normalize(answer) not in {normalize(item) for item in answers}:
        answers.append(answer)
    existing["answers"] = answers
    existing["options_seen"] = options
    existing["last_source"] = source
    existing["last_status"] = status
    existing["last_seen_at"] = now
    write_json(LOCAL_BANK_FILE, records)


def choose_answer(state: Dict[str, Any], extension_timeout: int) -> Optional[Dict[str, Any]]:
    ext = extension_answer(state, extension_timeout)
    if ext:
        return ext

    question = state["question"]
    options = [item["text"] for item in state["options"]]
    bank = find_bank_answer(question, options)
    if bank:
        return bank
    return None


def click_question_answer(index: int, submit: bool) -> None:
    payload = json.dumps({"index": index}, ensure_ascii=False)
    wait_before_click("question answer click")
    result = json.loads(chrome_eval(QUESTION_CLICK_JS.replace("ARGUMENTS", payload)))
    if not result.get("ok"):
        raise DailyError(result.get("reason", "failed to click answer"))
    if submit:
        # The submit button starts disabled and becomes enabled after answer selection.
        # Poll up to 3 seconds for it to become clickable.
        _, max_click_wait = click_wait_range()
        deadline = time.time() + 3 + max_click_wait
        while time.time() < deadline:
            time.sleep(0.5)
            wait_before_click("question submit click")
            js = SUBMIT_CLICK_JS.replace('"SUBMIT_SELECTOR"', json.dumps(QUESTION_SUBMIT_SPAN_SELECTOR))
            result = json.loads(chrome_eval(js))
            if result.get("ok"):
                return
            if result.get("reason") != "submit button still disabled":
                raise DailyError(result.get("reason", "failed to click submit"))
        raise DailyError("submit button did not become enabled in time")


def run_question(submit: bool, extension_timeout: int) -> str:
    chrome_open(QUESTION_URL)
    wait_for_url(QUESTION_URL)
    wait_for_ready()
    time.sleep(2)
    state = wait_for_question()
    if state.get("alreadyDone"):
        log("Question already appears complete.")
        return "already_done"

    question = state["question"]
    options = [item["text"] for item in state["options"]]
    log(f"Question: {question}")
    for idx, option in enumerate(options):
        log(f"Option {idx}: {option}")

    answer = choose_answer(state, extension_timeout)
    if not answer:
        remember_answer(question, options, "", "none", "unanswered")
        raise DailyError("No known answer from extension or local question bank.")

    log(f"Selected answer from {answer['source']}: [{answer['index']}] {answer['answer']}")
    remember_answer(question, options, answer["answer"], answer["source"], "selected")
    if submit:
        click_question_answer(answer["index"], submit=True)
        status = wait_for_question_submit_status()
        remember_answer(
            question,
            options,
            answer["answer"],
            answer["source"],
            status,
            confirm_answer=(status == "success"),
        )
        log(f"Question submit status: {status}")
        return status
    click_question_answer(answer["index"], submit=False)
    deadline = time.time() + 3
    after_select: Dict[str, Any] = {}
    while time.time() < deadline:
        time.sleep(0.5)
        after_select = page_state()
        raise_if_manual_attention_required(after_select, "Question page after selecting answer")
        if after_select.get("submitDisabled") is False:
            break
    else:
        raise DailyError(
            f"dry-run selected answer, but submit button did not become enabled ({state_summary(after_select)})"
        )
    log("Question dry-run selected answer and submit button is enabled.")
    return "dry-run"


def env_messages() -> List[str]:
    raw = os.environ.get("DAILY_CHECKIN_MESSAGES", "").strip()
    if raw:
        messages = [item.strip() for item in raw.split("|") if item.strip()]
        if messages:
            return messages
    return CHECKIN_MESSAGES


def run_checkin(submit: bool, message: Optional[str], open_new_tab: bool = False) -> str:
    message = message or random.choice(env_messages())
    chrome_open(CHECKIN_URL, new_tab=open_new_tab)
    wait_for_url(CHECKIN_URL)
    wait_for_ready()
    time.sleep(2)
    state = checkin_state()
    raise_if_manual_attention_required(state, "Check-in page")
    if checkin_already_done(state):
        log("Check-in already appears complete.")
        return "already_done"
    if not state.get("hasTextarea") or not state.get("hasCheckinSubmit"):
        raise DailyError(f"Could not find an actionable check-in form ({state_summary(state)}).")

    payload = json.dumps({"message": message, "submit": submit}, ensure_ascii=False)
    checkin_js = CHECKIN_JS.replace('"SUBMIT_SELECTOR"', json.dumps(CHECKIN_SUBMIT_SPAN_SELECTOR))
    if submit:
        wait_before_click("check-in submit click")
    result = json.loads(chrome_eval(checkin_js.replace("ARGUMENTS", payload)))
    if not result.get("ok"):
        raise DailyError(result.get("reason", "failed to fill check-in"))
    if submit:
        status = wait_for_checkin_submit_status()
        log(f"Check-in status: {status}")
        return status
    log(f"Check-in dry-run filled message: {message}")
    return "dry-run"


def run(args: argparse.Namespace) -> int:
    if args.random_start_delay:
        wait_before_launch_start()
    set_control_mode(args.control)
    submit = args.submit
    failed = False
    manual_attention = False
    question_manual_attention = False
    if CHROME_CONTROL_MODE == "cdp":
        log(
            "Using CDP Chrome profile: "
            f"{CHROME_USER_DATA_DIR} ({CHROME_CDP_ADDRESS}:{CHROME_CDP_PORT})"
        )
    else:
        profile = CHROME_PROFILE_DIRECTORY or "Chrome default"
        log(f"Using Chrome profile directory: {profile}")

    try:
        q_status = run_question(submit=submit, extension_timeout=args.extension_timeout)
    except ManualAttentionError as exc:
        q_status = f"manual_attention: {exc}"
        manual_attention = True
        question_manual_attention = True
        log(f"Question needs manual attention: {exc}")
    except Exception as exc:
        q_status = f"error: {exc}"
        failed = True
        log(f"Question failed: {exc}")

    try:
        if question_manual_attention:
            log("Opening check-in in a new tab to preserve the question page for manual attention.")
        c_status = run_checkin(
            submit=submit,
            message=args.checkin_message,
            open_new_tab=question_manual_attention,
        )
    except ManualAttentionError as exc:
        c_status = f"manual_attention: {exc}"
        manual_attention = True
        log(f"Check-in needs manual attention: {exc}")
    except Exception as exc:
        c_status = f"error: {exc}"
        failed = True
        log(f"Check-in failed: {exc}")

    log(f"Done: question={q_status}, checkin={c_status}, submit={submit}")

    # Log a consolidated success message if both tasks are completed (already_done or success)
    success_states = {"already_done", "success"}
    if q_status in success_states and c_status in success_states:
        log("Success: All daily tasks (Question and Check-in) are completed!")
        if CHROME_CONTROL_MODE == "cdp":
            closed = cdp_controller().close_opened_targets()
            if closed:
                log(f"Closed {closed} Chrome tab(s) opened by this run.")

    if manual_attention:
        return 2
    return 1 if failed else 0


def setup_cdp(args: argparse.Namespace) -> int:
    set_control_mode("cdp")
    url = args.url or CHECKIN_URL
    log(
        "Starting dedicated CDP Chrome profile: "
        f"{CHROME_USER_DATA_DIR} ({CHROME_CDP_ADDRESS}:{CHROME_CDP_PORT})"
    )
    already_running = False
    try:
        http_json("/json/version", timeout=1.0)
        already_running = True
    except Exception:
        pass

    controller = cdp_controller()
    controller.ensure_running(start_url=None if already_running else url)
    if already_running:
        controller.open(url)
    version = http_json("/json/version", timeout=2.0)
    log(f"CDP endpoint is available: {version.get('Browser', 'Chrome')}")
    log("If this is the first run, log in and complete any verification in the opened Chrome window.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run 1Point3Acres daily tasks through logged-in Chrome")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run daily question and check-in")
    run_parser.add_argument("--submit", action="store_true", help="Actually submit answer and check-in")
    run_parser.add_argument(
        "--control",
        choices=("applescript", "cdp"),
        default=CHROME_CONTROL_MODE,
        help="Chrome control backend",
    )
    run_parser.add_argument(
        "--extension-timeout",
        type=int,
        default=CHROME_EXTENSION_TIMEOUT,
        help="Seconds to wait for extension answer",
    )
    run_parser.add_argument(
        "--random-start-delay",
        action="store_true",
        help="Wait a random interval before starting the daily run",
    )
    run_parser.add_argument("--checkin-message", help="Override check-in text")
    run_parser.set_defaults(func=run)

    setup_parser = subparsers.add_parser("setup-cdp", help="Open the dedicated CDP Chrome profile")
    setup_parser.add_argument("--url", default=CHECKIN_URL, help="URL to open after starting Chrome")
    setup_parser.set_defaults(func=setup_cdp)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("Cancelled.")
        return 130
    except ManualAttentionError as exc:
        log(f"Manual attention required: {exc}")
        return 2
    except DailyError as exc:
        log(f"Failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
