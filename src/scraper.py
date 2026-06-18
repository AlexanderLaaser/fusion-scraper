#!/usr/bin/env python3
"""
Fusion Festival Forum Scraper
Monitors the ticket/marketplace forum for posts matching keywords and sends Telegram alerts.
Loops through up to 5 configured accounts, posting per-account reply content on each match.
"""

import csv
import json
import logging
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(dotenv_path=ROOT / ".env")

FORUM_BASE = "https://forum.fusion-festival.de"
FORUM_ID = 82
PAGE_SIZE = 25

KEYWORDS = [kw for kw in (kw.strip().lower() for kw in os.getenv("KEYWORDS", "biete,verkaufe,abzugeben,tausche").split(",")) if kw]
IGNORE_KEYWORDS = [kw for kw in (kw.strip().lower() for kw in os.getenv("IGNORE_KEYWORDS", "at.tension,suche").split(",")) if kw]
MAX_PAGES = int(os.getenv("MAX_PAGES", "3"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Load up to 5 account+content pairs — skips any slot missing user, pass, or content file
ACCOUNTS: list[dict] = []
for _i in range(1, 6):
    _user = os.getenv(f"FORUM_USERNAME_{_i}", "")
    _pass = os.getenv(f"FORUM_PASSWORD_{_i}", "")
    _cf   = ROOT / "contents" / f"content{_i}.md"
    _text = _cf.read_text(encoding="utf-8").strip() if _cf.exists() else ""
    if _user and _pass and _text:
        ACCOUNTS.append({"index": _i, "user": _user, "password": _pass, "content": _text})

STATE_FILE   = ROOT / "threads" / "seen_threads.json"
REPLIED_FILE = ROOT / "threads" / "replied_threads.json"
EXEC_LOG     = ROOT / "logs" / "execution.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


def load_replied() -> dict:
    if not REPLIED_FILE.exists():
        return {}
    data = json.loads(REPLIED_FILE.read_text())
    if isinstance(data, list):
        # Legacy bare-list format — treat all accounts as done
        return {
            tid: {
                "title": None,
                "url": f"{FORUM_BASE}/viewtopic.php?t={tid}",
                "author": None,
                "timestamp": None,
                "matched_keywords": [],
                "accounts_replied": list(range(1, 6)),
            }
            for tid in data
        }
    migrated: dict = {}
    for tid, d in data.items():
        if not isinstance(d, dict):
            continue
        if "accounts_replied" not in d:
            # Migrate single-account format:
            # replied=true  → all 5 slots done (don't re-reply with new accounts)
            # replied=false → none done (all accounts will retry)
            d["accounts_replied"] = list(range(1, 6)) if d.get("replied") else []
            d.pop("replied", None)
            d.pop("reason", None)
        migrated[tid] = d
    return migrated


def save_replied(replied: dict) -> None:
    REPLIED_FILE.write_text(json.dumps(replied, indent=2, ensure_ascii=False))


def login(session: requests.Session, user: str, password: str) -> str:
    """Returns session sid on success, empty string on failure."""
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
        "username":      user,
        "password":      password,
        "form_token":    field("form_token"),
        "creation_time": field("creation_time"),
        "sid":           field("sid"),
        "redirect":      field("redirect"),
        "login":         "Anmelden",
    }

    time.sleep(2)  # phpBB rejects forms submitted faster than ~1s after render

    r = session.post(action, data=payload, timeout=15, headers={"Referer": login_url})
    r.raise_for_status()

    if "mode=logout" not in r.text and "Abmelden" not in r.text:
        log.warning("Login FAILED for %s — skipping account", user)
        return ""

    m = re.search(r"[?&]sid=([a-f0-9]+)", r.url)
    sid = m.group(1) if m else ""
    log.info("Login OK as %s (sid=%s)", user, sid[:8] + "...")
    return sid


def post_reply(session: requests.Session, topic_id: str, sid: str, reply_text: str) -> bool:
    url = f"{FORUM_BASE}/posting.php?mode=reply&t={topic_id}&sid={sid}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    form = soup.find("form", {"id": "postform"})
    if not form:
        log.error("Reply form not found for t=%s", topic_id)
        return False

    action = form.get("action", "").replace("./", f"{FORUM_BASE}/", 1)

    payload: dict[str, str] = {}
    for inp in form.find_all("input", {"type": "hidden"}):
        if inp.get("name"):
            payload[inp["name"]] = inp.get("value", "")

    subject_el = form.find("input", {"name": "subject"})
    payload["subject"] = subject_el["value"] if subject_el else ""
    payload["message"] = reply_text
    payload["mode"]    = "reply"
    payload["t"]       = topic_id
    payload["post"]    = "Absenden"

    time.sleep(2)  # same CSRF timing requirement as login

    r = session.post(action, data=payload, timeout=15, headers={"Referer": url})
    r.raise_for_status()

    success = "viewtopic" in r.url or "viewtopic" in r.text[:500]
    if success:
        log.info("Reply posted to t=%s", topic_id)
    else:
        log.warning("Reply may have failed for t=%s (status %s)", topic_id, r.status_code)
    return success


def scrape_page(session: requests.Session, page: int) -> list[dict]:
    url = f"{FORUM_BASE}/viewforum.php?f={FORUM_ID}&start={page * PAGE_SIZE}"
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
    if any(kw in title_lower for kw in IGNORE_KEYWORDS):
        return False
    return any(kw in title_lower for kw in KEYWORDS)


def write_exec_log(match_count: int) -> None:
    header = not EXEC_LOG.exists()
    with EXEC_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if header:
            w.writerow(["timestamp", "matches"])
        w.writerow([datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S"), match_count])


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

    scrape_session = requests.Session()
    scrape_session.headers["User-Agent"] = "Mozilla/5.0 (compatible; fusion-monitor/1.0)"

    new_matches: list[dict] = []
    new_count = 0

    for page in range(MAX_PAGES):
        try:
            threads = scrape_page(scrape_session, page)
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
            new_matches.append(t)

            matched_kws = [kw for kw in KEYWORDS if kw in t["title"].lower()]
            replied.setdefault(t["id"], {
                "title":            t["title"],
                "url":              t["url"],
                "author":           t["author"] or "unknown",
                "timestamp":        datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S"),
                "matched_keywords": matched_kws,
                "accounts_replied": [],
            })

        time.sleep(1.5)

    save_seen(seen)

    if not ACCOUNTS:
        log.info("No accounts configured — skipping reply phase")
        log.info("Done — %d new match(es)", new_count)
        write_exec_log(new_count)
        return

    # Reply phase — one session per account, login once, reply to all pending threads
    for account in ACCOUNTS:
        pending = [
            t for t in new_matches
            if account["index"] not in replied[t["id"]].get("accounts_replied", [])
        ]
        if not pending:
            log.info("Account %d (%s): no pending threads", account["index"], account["user"])
            continue

        log.info("Account %d (%s): %d thread(s) to reply", account["index"], account["user"], len(pending))
        acc_session = requests.Session()
        acc_session.headers["User-Agent"] = "Mozilla/5.0 (compatible; fusion-monitor/1.0)"
        sid = login(acc_session, account["user"], account["password"])

        if not sid:
            continue

        for t in pending:
            time.sleep(1)
            if post_reply(acc_session, t["id"], sid, account["content"]):
                replied[t["id"]]["accounts_replied"].append(account["index"])
                save_replied(replied)
            time.sleep(0.5)

    save_replied(replied)
    log.info("Done — %d new match(es)", new_count)
    write_exec_log(new_count)


if __name__ == "__main__":
    main()
