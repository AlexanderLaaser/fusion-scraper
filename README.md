# Fusion Festival Forum Scraper

Überwacht das [Fusion Festival Marktplatz-Forum](https://forum.fusion-festival.de/viewforum.php?f=82) auf neue Posts mit bestimmten Keywords und schickt eine Telegram-Benachrichtigung.

## Features

- Scannt die ersten `MAX_PAGES` Seiten des Forums alle 5 Minuten (via Cron)
- Filtert Threadtitel nach konfigurierbaren Keywords
- Sendet Telegram-Nachricht bei Treffern — und eine Info-Nachricht wenn kein Treffer gefunden wurde
- Verhindert Duplikate via `seen_threads.json`
- Schreibt einen Execution Log (`execution.log`) mit Zeitstempel und Anzahl Treffer pro Lauf

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # oder .env direkt anlegen
```

### .env befüllen

```env
TELEGRAM_BOT_TOKEN=123456789:AABBccddeeff...
TELEGRAM_CHAT_ID=987654321
KEYWORDS=biete,verkaufe,abzugeben,tausche
MAX_PAGES=3
```

**Telegram-Bot einrichten:**
1. `@BotFather` in Telegram → `/newbot` → Token kopieren
2. Bot einmal anschreiben (`/start`)
3. Token in Browser aufrufen: `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id` aus der Antwort

## Ausführen

```bash
# Einmalig testen
.venv/bin/python scraper.py

# Cron — alle 5 Minuten (Pfad anpassen)
*/5 * * * * /pfad/zu/fusion-scraper/.venv/bin/python /pfad/zu/fusion-scraper/scraper.py >> /pfad/zu/fusion-scraper/fusion-scraper.log 2>&1
```

## Dateien

| Datei | Beschreibung |
|---|---|
| `scraper.py` | Hauptskript |
| `.env` | Credentials (nicht eingecheckt) |
| `seen_threads.json` | Bereits gesehene Thread-IDs (auto-generiert) |
| `execution.log` | Ein Eintrag pro Lauf: Zeitstempel + Treffer-Anzahl |
| `fusion-scraper.log` | Detailliertes Laufzeit-Log (stdout/stderr des Cron) |

## Reset

Alle gesehenen Threads vergessen und erneut alle Treffer melden:

```bash
rm seen_threads.json
```
