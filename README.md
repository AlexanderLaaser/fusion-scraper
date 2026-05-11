# Fusion Festival Forum Scraper

Überwacht das [Fusion Festival Marktplatz-Forum](https://forum.fusion-festival.de/viewforum.php?f=82) auf neue Posts mit bestimmten Keywords, schickt eine Telegram-Benachrichtigung und antwortet automatisch auf Treffer.

## Features

- Scannt die ersten `MAX_PAGES` Seiten des Forums alle 5 Minuten (via Cron)
- Filtert Threadtitel nach konfigurierbaren Keywords
- Sendet Telegram-Nachricht bei Treffern
- Loggt sich ins Forum ein und postet automatisch eine Antwort (`content.md`)
- Verhindert Duplikate via `threads/seen_threads.json`
- Verhindert doppelte Replies via `threads/replied_threads.json`
- Schreibt einen Execution Log (`logs/execution.log`) mit Zeitstempel und Anzahl Treffer pro Lauf

## Projektstruktur

```
fusion-scraper/
├── src/scraper.py          # Hauptskript
├── threads/
│   ├── seen_threads.json   # Bereits gesehene Thread-IDs (auto-generiert)
│   └── replied_threads.json# Bereits beantwortete Thread-IDs (auto-generiert)
├── logs/
│   ├── execution.log       # Ein Eintrag pro Lauf: Zeitstempel + Treffer-Anzahl
│   └── fusion-scraper.log  # Laufzeit-Log (stdout/stderr des Cron)
├── content.md              # Reply-Text der automatisch gepostet wird
├── .env                    # Credentials (nicht eingecheckt)
└── requirements.txt
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### .env befüllen

```env
TELEGRAM_BOT_TOKEN=123456789:AABBccddeeff...
TELEGRAM_CHAT_ID=987654321

FORUM_USERNAME=dein_username
FORUM_PASSWORD=dein_passwort

KEYWORDS=biete,verkaufe,abzugeben,tausche
MAX_PAGES=3
```

**Telegram-Bot einrichten:**

1. `@BotFather` in Telegram → `/newbot` → Token kopieren
2. Bot einmal anschreiben (`/start`)
3. Token in Browser aufrufen: `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id` aus der Antwort

### Reply-Text anpassen

Den Inhalt von `content.md` bearbeiten — dieser Text wird automatisch als Antwort auf passende Threads gepostet.

## Ausführen

```bash
# Einmalig testen
.venv/bin/python src/scraper.py

# Cron — alle 5 Minuten (Pfad anpassen)
*/5 * * * * /pfad/zu/fusion-scraper/.venv/bin/python /pfad/zu/fusion-scraper/src/scraper.py >> /pfad/zu/fusion-scraper/logs/fusion-scraper.log 2>&1
```

## Reset

Alle gesehenen Threads vergessen und erneut alle Treffer melden:

```bash
rm threads/seen_threads.json threads/replied_threads.json
```
