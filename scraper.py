#!/usr/bin/env python3
"""
Fusion Festival Forum Scraper
Monitors the ticket/marketplace forum for posts matching keywords and sends Telegram alerts.
Optionally logs in and posts a reply on matching threads.
Run via cron, e.g. every 5 min: */5 * * * * /path/to/venv/bin/python /path/to/scraper.py
"""

import csv
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

FORUM_BASE = "https://forum.fusion-festival.de"
FORUM_ID = 82
PAGE_SIZE = 25

KEYWORDS = [kw.strip().lower() for kw in os.getenv("KEYWORDS", "biete,verkaufe,abzugeben,tausche").split(",")]
MAX_PAGES = int(os.getenv("MAX_PAGES", "3"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

FORUM_USER = os.getenv("FORUM_USERNAME", "")
FORUM_PASS = os.getenv("FORUM_PASSWORD", "")
_content_file = Path(__file__).parent / "content.md"
REPLY_TEXT = _content_file.read_text(encoding="utf-8").strip() if _content_file.exists() else os.getenv("REPLY_TEXT", "")

STATE_FILE   = Path(__file__).parent / "seen_threads.json"
REPLIED_FILE = Path(__file__).parent / "replied_threads.json"
EXEC_LOG     = Path(__file__).parent / "execution.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


def load_replied() -> dict[str, dict]:
    if REPLIED_FILE.exists():
        data = json.loads(REPLIED_FILE.read_text())
        if isinstance(data, list):
            return {
                tid: {
                    "title": None,
                    "url": f"{FORUM_BASE}/viewtopic.php?t={tid}",
                    "author": None,
                    "timestamp": None,
                    "matched_keywords": [],
                    "replied": True,
                    "reason": "replied — migrated from legacy format",
                }
                for tid in data
            }
        return data
    return {}


def save_replied(replied: dict[str, dict]) -> None:
    REPLIED_FILE.write_text(json.dumps(replied, indent=2, ensure_ascii=False))


def login(session: requests.Session) -> str:
    """Returns the session sid on success, empty string on failure."""
    if not FORUM_USER or not FORUM_PASS:
        return ""

    login_url = f"{FORUM_BASE}/ucp.php?mode=login"
    r = session.get(login_url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    form = soup.find("form", {"id": "login"})
    if not form:
        log.error("Login form not found")
        return ""

    action = form.get("action", "").replace("./", f"{FORUM_BASE}/", 1)

    def field(name: str) -> str:
        el = form.find("input", {"name": name})
        return el["value"] if el else ""

    payload = {
        "username":      FORUM_USER,
        "password":      FORUM_PASS,
        "form_token":    field("form_token"),
        "creation_time": field("creation_time"),
        "sid":           field("sid"),
        "redirect":      field("redirect"),
        "login":         "Anmelden",
    }

    # phpBB rejects forms submitted faster than ~1s after render
    time.sleep(2)

    r = session.post(action, data=payload, timeout=15,
                     headers={"Referer": login_url})
    r.raise_for_status()

    if "mode=logout" not in r.text and "Abmelden" not in r.text:
        log.warning("Login FAILED — continuing unauthenticated")
        return ""

    # Forum uses URL-based sessions — extract sid from redirect URL
    m = re.search(r"[?&]sid=([a-f0-9]+)", r.url)
    sid = m.group(1) if m else ""
    log.info("Login OK as %s (sid=%s)", FORUM_USER, sid[:8] + "...")
    return sid


def post_reply(session: requests.Session, topic_id: str, sid: str) -> bool:
    url = f"{FORUM_BASE}/posting.php?mode=reply&t={topic_id}&sid={sid}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    form = soup.find("form", {"id": "postform"})
    if not form:
        log.error("Reply form not found for t=%s", topic_id)
        return False

    action = form.get("action", "").replace("./", f"{FORUM_BASE}/", 1)

    # Carry all hidden fields to satisfy CSRF and phpBB internal state
    payload: dict[str, str] = {}
    for inp in form.find_all("input", {"type": "hidden"}):
        if inp.get("name"):
            payload[inp["name"]] = inp.get("value", "")

    # subject from the form (pre-filled as "Re: <original title>")
    subject_el = form.find("input", {"name": "subject"})
    payload["subject"] = subject_el["value"] if subject_el else ""
    payload["message"] = REPLY_TEXT
    payload["mode"]    = "reply"
    payload["t"]       = topic_id
    payload["post"]    = "Absenden"  # phpBB submit button name is "post"

    time.sleep(2)  # same CSRF timing requirement as login

    r = session.post(action, data=payload, timeout=15,
                     headers={"Referer": url})
    r.raise_for_status()

    success = "viewtopic" in r.url or "viewtopic" in r.text[:500]
    if success:
        log.info("Reply posted to t=%s", topic_id)
    else:
        log.warning("Reply may have failed for t=%s (status %s)", topic_id, r.status_code)
    return success


def scrape_page(session: requests.Session, page: int, sid: str = "") -> list[dict]:
    url = f"{FORUM_BASE}/viewforum.php?f={FORUM_ID}&start={page * PAGE_SIZE}"
    if sid:
        url += f"&sid={sid}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    seen_ids: set[str] = set()
    threads: list[dict] = []

    for link in soup.find_all("a", href=re.compile(r"viewtopic\.php")):
        title = link.get_text(strip=True)
        if not title:
            continue

        m = re.search(r"[?&]t=(\d+)", link["href"])
        if not m:
            continue
        topic_id = m.group(1)
        if topic_id in seen_ids:
            continue
        seen_ids.add(topic_id)

        author = ""
        row = link.find_parent("li") or link.find_parent("tr")
        if row:
            a = row.find("a", href=re.compile(r"memberlist\.php"))
            if a:
                author = a.get_text(strip=True)

        threads.append({
            "id":     topic_id,
            "title":  title,
            "url":    f"{FORUM_BASE}/viewtopic.php?t={topic_id}",
            "author": author,
        })

    return threads


def matches(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in KEYWORDS)


def write_exec_log(match_count: int) -> None:
    header = not EXEC_LOG.exists()
    with EXEC_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if header:
            w.writerow(["timestamp", "matches"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), match_count])


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — printing to stdout instead")
        print(text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    if r.ok:
        log.info("Telegram sent: %.60s", text)
    else:
        log.error("Telegram error %s: %s", r.status_code, r.text[:200])


def notify(title: str, author: str, url: str) -> None:
    text = (
        f"🎪 *Fusion Forum — neuer Treffer*\n\n"
        f"*{title}*\n"
        f"von {author or 'unbekannt'}\n\n"
        f"[Zum Post]({url})"
    )
    send_telegram(text)


def main() -> None:
    seen    = load_seen()
    replied = load_replied()
    replied_ids = {tid for tid, d in replied.items() if d.get("replied")}
    needs_save  = any(d.get("reason") == "replied — migrated from legacy format" for d in replied.values())

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; fusion-monitor/1.0)"

    auto_reply = bool(FORUM_USER and FORUM_PASS and REPLY_TEXT)
    sid        = login(session) if auto_reply else ""

    new_count = 0

    for page in range(MAX_PAGES):
        try:
            threads = scrape_page(session, page, sid)
        except requests.RequestException as e:
            log.error("Failed to fetch page %d: %s", page, e)
            break

        log.info("Page %d: %d threads", page + 1, len(threads))

        for t in threads:
            if t["id"] in seen:
                continue
            seen.add(t["id"])

            if not matches(t["title"]):
                continue

            log.info("MATCH: %s", t["title"])
            notify(t["title"], t["author"], t["url"])
            new_count += 1

            if t["id"] in replied_ids:
                continue

            matched_kws = [kw for kw in KEYWORDS if kw in t["title"].lower()]
            entry: dict = {
                "title":            t["title"],
                "url":              t["url"],
                "author":           t["author"] or "unknown",
                "timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "matched_keywords": matched_kws,
                "replied":          False,
                "reason":           "",
            }

            if not auto_reply:
                entry["reason"] = "auto-reply disabled — credentials not configured"
            elif not sid:
                entry["reason"] = "auto-reply skipped — login failed"
            else:
                time.sleep(1)
                if post_reply(session, t["id"], sid):
                    entry["replied"] = True
                    entry["reason"]  = f"replied — matched: {', '.join(matched_kws)}"
                    replied_ids.add(t["id"])
                else:
                    entry["reason"] = "post_reply failed — check logs for details"

            replied[t["id"]] = entry
            save_replied(replied)
            needs_save = False

            time.sleep(0.5)

        time.sleep(1.5)

    save_seen(seen)
    if needs_save:
        save_replied(replied)
    log.info("Done — %d new match(es)", new_count)

    write_exec_log(new_count)


if __name__ == "__main__":
    main()
