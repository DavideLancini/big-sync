# big-sync

Aggregatore intelligente di messaggi e attività multi-piattaforma con automazione AI.

## Cosa fa

big-sync si connette a 5 macro fonti di dati, analizza il contenuto tramite workflow Gemini dedicati e agisce automaticamente su Google Workspace (Contacts, Calendar, Tasks).

---

## Fonti dati

| # | Fonte | Libreria | Tipo di contenuto |
|---|-------|----------|-------------------|
| 1 | **WhatsApp Business** | WhatsApp Business Cloud API (Meta) | Tutti i messaggi ricevuti sul numero business — chat, gruppi, media |
| 2 | **Telegram** | Telethon / Pyrogram (MTProto client) | Tutti i messaggi ricevuti — chat, gruppi, canali |
| 3 | **Email** | IMAP | Caselle IMAP/SMTP multiple |
| 4 | **Microsoft Teams** | Microsoft Graph API | Messaggi, canali, chat dirette |
| 5 | **ClickUp** | ClickUp API + Webhook | Task, commenti, spazi, liste |
| 6 | **SMS** | Twilio / SIM locale | SMS ricevuti sul numero |
| 7 | **GitHub** | GitHub Webhooks + REST API | Issue, PR, commenti, review, release |
| 8 | **Google Drive** | Google Drive API v3 | Documenti modificati, commenti, nuovi file |
| 9 | **Home Assistant** | Webhook / REST API | Automazioni, eventi, stati dispositivi |
| 10 | **RSS Feed** | Polling HTTP | Articoli, aggiornamenti da blog e siti |

### Fonti in sola lettura (deduplicazione)

Queste fonti vengono **lette prima di scrivere** per evitare duplicati. Non hanno workflow AI dedicato — la logica risiede in `common`.

| Fonte | Scopo |
|-------|-------|
| **Google Contacts** | Verificare se il contatto esiste già prima di crearlo |
| **Google Calendar** | Verificare se l'evento esiste già prima di crearlo |
| **Google Tasks** | Verificare se il todo esiste già prima di crearlo |

---

## Pipeline AI

Per ogni fonte esiste un workflow Gemini dedicato che:

1. **Ingerisce** il contenuto grezzo dalla fonte
2. **Analizza** il testo per estrarre entità, intenzioni e scadenze
3. **Decide** quale azione compiere tra le tre disponibili
4. **Esegue** la scrittura sulle API Google

### Workflow

| Workflow | Fonte | Modello |
|----------|-------|---------|
| `workflow_whatsapp` | WhatsApp Business | Gemini |
| `workflow_telegram` | Telegram | Gemini |
| `workflow_email` | Caselle email | Gemini |
| `workflow_teams` | Microsoft Teams | Gemini |
| `workflow_clickup` | ClickUp | Gemini |
| `workflow_sms` | SMS | Gemini |
| `workflow_github` | GitHub | Gemini |
| `workflow_drive` | Google Drive | Gemini |
| `workflow_home_assistant` | Home Assistant | Gemini |
| `workflow_rss` | RSS Feed | Gemini |

---

## Output (azioni automatiche)

### Google Contacts
- Creazione e aggiornamento contatti estratti da messaggi e firme email
- Arricchimento con numero di telefono, azienda, ruolo

### Google Calendar
- Creazione eventi da date/orari menzionati nei messaggi
- Parsing linguaggio naturale (es. "ci vediamo giovedì alle 15")
- Impostazione partecipanti e link meet quando disponibili

### Google Tasks
- Creazione todo da richieste, promesse e action item rilevati
- Assegnazione scadenze quando esplicite nel testo
- Collegamento alla fonte originale (messaggio, email, task ClickUp)

### Deduplicazione (app `common`)
Prima di ogni scrittura su Contacts / Calendar / Tasks, `common` interroga le API Google per verificare che il dato non esista già. La scrittura avviene solo in caso di assenza o aggiornamento necessario.

---

## Stack tecnico

- **Backend**: Django 6 (Python)
- **AI**: Google Gemini API (un workflow per fonte)
- **Output**: Google Workspace APIs (Contacts, Calendar, Tasks)
- **Fonti**: WhatsApp Business Cloud API (Meta), Telethon/Pyrogram (Telegram MTProto), IMAP/SMTP, Microsoft Graph API, ClickUp API
- **Nota WhatsApp**: unico numero business → tutti i messaggi in arrivo transitano già dall'API ufficiale Meta tramite webhook. Nessuna libreria non ufficiale necessaria.
- **Nota Telegram**: Telethon/Pyrogram usano l'API MTProto ufficiale come client utente — legge tutti i messaggi senza necessità di bot.

---

## Struttura del progetto

```
big-sync/
├── config/                  # Configurazione Django
├── common/                  # Utilities condivise + logica dedup (Contacts/Calendar/Tasks)
├── sources/                 # Connettori per le 10 fonti (app Django indipendenti)
│   ├── whatsapp/
│   ├── telegram/
│   ├── email_source/
│   ├── teams/
│   ├── clickup/
│   ├── sms/
│   ├── github/
│   ├── drive/
│   ├── home_assistant/
│   └── rss/
├── workflows/               # Workflow Gemini (uno per fonte)
│   ├── workflow_whatsapp.py
│   ├── workflow_telegram.py
│   ├── workflow_email.py
│   ├── workflow_teams.py
│   ├── workflow_clickup.py
│   ├── workflow_sms.py
│   ├── workflow_github.py
│   ├── workflow_drive.py
│   ├── workflow_home_assistant.py
│   └── workflow_rss.py
├── outputs/                 # Scrittura su Google Workspace
│   ├── contacts.py
│   ├── calendar.py
│   └── tasks.py
└── manage.py
```
