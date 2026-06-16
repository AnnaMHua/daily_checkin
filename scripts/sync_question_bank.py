#!/usr/bin/env python3
"""Fetch public 1Point3Acres daily-question banks into data/question_bank.json."""

import ast
import html
import json
import re
import sys
import urllib.request
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BANK_FILE = DATA_DIR / "question_bank.json"

SOURCES = {
    "cnblogs": "https://www.cnblogs.com/shoufeng/p/19078421",
    "github": "https://raw.githubusercontent.com/mageLi/1Point3Acres_Daily_Question/master/README.md",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"^[〖【]?\s*题目\s*[〗】]?", "", value)
    value = value.lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[\"'`‘’“”·,，.。:：;；!！?？()\[\]{}<>《》、/\\|-]", "", value)
    return value


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def add_record(records: Dict[str, Dict[str, Any]], question: str, answers: Iterable[str], source: str) -> None:
    question = html.unescape(re.sub(r"\s+", " ", question)).strip()
    answers = [html.unescape(re.sub(r"\s+", " ", str(item))).strip() for item in answers if str(item).strip()]
    if not question or not answers:
        return
    key = normalize(question)
    record = records.setdefault(key, {"question": question, "answers": [], "sources": []})
    existing = {normalize(item) for item in record["answers"]}
    for answer in answers:
        if normalize(answer) not in existing:
            record["answers"].append(answer)
            existing.add(normalize(answer))
    if source not in record["sources"]:
        record["sources"].append(source)


def parse_cnblogs(text: str) -> List[Dict[str, Any]]:
    body_start = text.find('id="cnblogs_post_body"')
    if body_start >= 0:
        text = text[body_start:]
    plain = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    plain = re.sub(r"</p>|</div>|</li>|</pre>", "\n", plain, flags=re.I)
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = html.unescape(plain)
    start = plain.find("QA = {")
    if start < 0:
        return []
    end = plain.find("}", start)
    if end < 0:
        return []
    block = plain[start + len("QA = ") : end + 1]
    try:
        data = ast.literal_eval(block)
    except Exception:
        return []
    records = []
    for question, answer in data.items():
        answers = answer if isinstance(answer, list) else [answer]
        records.append({"question": str(question), "answers": [str(item) for item in answers], "source": "cnblogs"})
    return records


def parse_github_markdown(text: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    current_question = ""
    current_answers: List[str] = []

    def flush() -> None:
        nonlocal current_question, current_answers
        if current_question and current_answers:
            records.append({"question": current_question, "answers": current_answers[:], "source": "github"})
        current_question = ""
        current_answers = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("###"):
            flush()
            current_question = re.sub(r"^#+\s*〖题目〗\s*", "", line).strip()
            continue
        if "✓" in line and current_question:
            answer = line.split("✓", 1)[1].strip()
            if answer:
                current_answers.append(answer)
    flush()
    return records


def main() -> int:
    records: Dict[str, Dict[str, Any]] = {}
    for source, url in SOURCES.items():
        text = fetch_text(url)
        if source == "cnblogs":
            parsed = parse_cnblogs(text)
        else:
            parsed = parse_github_markdown(text)
        for item in parsed:
            add_record(records, item["question"], item["answers"], item["source"])
        print(f"{source}: imported {len(parsed)} records")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = sorted(records.values(), key=lambda item: item["question"])
    with BANK_FILE.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(output)} merged records to {BANK_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
