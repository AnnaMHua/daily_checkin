#!/usr/bin/env python3
"""Daily 1Point3Acres automation through the user's logged-in Chrome.

This runner does not solve or bypass human verification. It operates only on a
normal, already logged-in Chrome page through AppleScript JavaScript execution.
"""

import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
PUBLIC_BANK_FILE = DATA_DIR / "question_bank.json"
LOCAL_BANK_FILE = DATA_DIR / "local_question_bank.json"

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


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


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


def chrome_eval(js: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as temp:
        temp.write(js)
        temp_path = temp.name
    try:
        script = (
            f'set jsCode to read POSIX file "{temp_path}"\n'
            'tell application "Google Chrome"\n'
            '  if not (exists window 1) then make new window\n'
            "  execute active tab of window 1 javascript jsCode\n"
            "end tell"
        )
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


def chrome_open(url: str) -> None:
    script = (
        'tell application "Google Chrome"\n'
        "  if not (exists window 1) then make new window\n"
        f'  set URL of active tab of window 1 to "{url}"\n'
        "end tell"
    )
    run_osascript(script)


def wait_for_url(url: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = chrome_eval("location.href")
        if current.startswith(url):
            return
        time.sleep(0.5)
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
        raise DailyError(f"{context} requires manual login or verification ({evidence_summary(reasons)}).")


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
    if question_form_is_actionable(state):
        return False
    return question_text_indicates_done(page_body(state))


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
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity) === 0) return false;
    if (el.getClientRects().length > 0) return true;
    return ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
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
      .filter(el => isVisible(el) || el.tagName === "INPUT");
    if (challengeElements.length) {
      evidence.push(`${challengeElements.length} captcha/challenge element(s)`);
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
  const answerButtons = buttons
    .map((button, domIndex) => ({ button, domIndex, text: cleanButtonText(button) }))
    .filter(item =>
      item.text &&
      item.button.className.includes("cursor-pointer") &&
      item.button.className.includes("rounded-md") &&
      item.button.className.includes("text-left") &&
      !item.text.includes("提交答案")
    )
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

  const submitSpan = document.querySelector(submitSelector) || Array.from(document.querySelectorAll("span"))
    .find(span => (span.textContent || "").replace(/\s+/g, "") === "提交答案");
  const submit = submitSpan
    ? submitSpan.closest("button")
    : buttons.find(button => cleanButtonText(button).replace(/\s+/g, "").includes("提交答案"));
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
  const buttons = Array.from(document.querySelectorAll("button"));
  const answerButtons = buttons
    .filter(button =>
      cleanButtonText(button) &&
      button.className.includes("cursor-pointer") &&
      button.className.includes("rounded-md") &&
      button.className.includes("text-left") &&
      !cleanButtonText(button).includes("提交答案")
    );
  if (!answerButtons[index]) return JSON.stringify({ ok: false, reason: "answer button not found" });
  const answer = answerButtons[index];
  answer.scrollIntoView({ block: "center", inline: "center" });
  answer.focus();
  fire(answer, "pointerdown");
  fire(answer, "mousedown");
  fire(answer, "mouseup");
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
        raise_if_manual_attention_required(state, "Question page after submit")
        status = question_submit_status(state)
        if status != "submitted":
            return status
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
        raise_if_manual_attention_required(state, "Check-in page after submit")
        if checkin_already_done(state):
            return "success"
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
    result = json.loads(chrome_eval(QUESTION_CLICK_JS.replace("ARGUMENTS", payload)))
    if not result.get("ok"):
        raise DailyError(result.get("reason", "failed to click answer"))
    if submit:
        # The submit button starts disabled and becomes enabled after answer selection.
        # Poll up to 3 seconds for it to become clickable.
        deadline = time.time() + 3
        while time.time() < deadline:
            time.sleep(0.5)
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
    time.sleep(1)
    after_select = page_state()
    raise_if_manual_attention_required(after_select, "Question page after selecting answer")
    if after_select.get("submitDisabled") is not False:
        raise DailyError("dry-run selected answer, but submit button did not become enabled")
    log("Question dry-run selected answer and submit button is enabled.")
    return "dry-run"


def env_messages() -> List[str]:
    raw = os.environ.get("DAILY_CHECKIN_MESSAGES", "").strip()
    if raw:
        messages = [item.strip() for item in raw.split("|") if item.strip()]
        if messages:
            return messages
    return CHECKIN_MESSAGES


def run_checkin(submit: bool, message: Optional[str]) -> str:
    message = message or random.choice(env_messages())
    chrome_open(CHECKIN_URL)
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
    submit = args.submit
    failed = False

    try:
        q_status = run_question(submit=submit, extension_timeout=args.extension_timeout)
    except Exception as exc:
        q_status = f"error: {exc}"
        failed = True
        log(f"Question failed: {exc}")

    try:
        c_status = run_checkin(submit=submit, message=args.checkin_message)
    except Exception as exc:
        c_status = f"error: {exc}"
        failed = True
        log(f"Check-in failed: {exc}")

    log(f"Done: question={q_status}, checkin={c_status}, submit={submit}")

    # Log a consolidated success message if both tasks are completed (already_done or success)
    success_states = {"already_done", "success"}
    if q_status in success_states and c_status in success_states:
        log("Success: All daily tasks (Question and Check-in) are completed!")

    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run 1Point3Acres daily tasks through logged-in Chrome")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run daily question and check-in")
    run_parser.add_argument("--submit", action="store_true", help="Actually submit answer and check-in")
    run_parser.add_argument("--extension-timeout", type=int, default=12, help="Seconds to wait for extension answer")
    run_parser.add_argument("--checkin-message", help="Override check-in text")
    run_parser.set_defaults(func=run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("Cancelled.")
        return 130
    except DailyError as exc:
        log(f"Failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
