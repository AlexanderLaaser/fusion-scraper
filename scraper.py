#!/usr/bin/env python3
"""
Fusion Festival Forum Scraper
Monitors the ticket/marketplace forum for posts matching keywords and sends Telegram alerts.
Run via cron, e.g. every 30 min: */30 * * * * /path/to/venv/bin/python /path/to/scraper.py
"""

import json
import logging
import os
import re
import time
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

STATE_FILE = Path(__file__).parent / "seen_threads.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


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
            "id": topic_id,
            "title": title,
            "url": f"{FORUM_BASE}/viewtopic.php?t={topic_id}",
            "author": author,
        })

    return threads


def matches(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in KEYWORDS)


def notify(title: str, author: str, url: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — printing to stdout instead")
        print(f"MATCH: {title} ({url})")
        return

    text = (
        f"🎪 *Fusion Forum — neuer Treffer*\n\n"
        f"*{title}*\n"
        f"von {author or 'unbekannt'}\n\n"
        f"[Zum Post]({url})"
    )
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    if r.ok:
        log.info("Telegram sent: %.60s", title)
    else:
        log.error("Telegram error %s: %s", r.status_code, r.text[:200])


def main() -> None:
    seen = load_seen()
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; fusion-monitor/1.0)"

    new_count = 0

    for page in range(MAX_PAGES):
        try:
            threads = scrape_page(session, page)
        except requests.RequestException as e:
            log.error("Failed to fetch page %d: %s", page, e)
            break

        log.info("Page %d: %d threads", page + 1, len(threads))

        for t in threads:
            if t["id"] in seen:
                continue
            seen.add(t["id"])

            if matches(t["title"]):
                log.info("MATCH: %s", t["title"])
                notify(t["title"], t["author"], t["url"])
                new_count += 1
                time.sleep(0.5)

        time.sleep(1.5)

    save_seen(seen)
    log.info("Done — %d new match(es)", new_count)


if __name__ == "__main__":
    main()
