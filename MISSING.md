# MISSING — Credenziali e configurazioni necessarie

Tutto ciò che serve per far funzionare big-sync end-to-end.

---

## 1. WhatsApp Business (Cloud API ufficiale Meta)

> Approccio: **WhatsApp Business Cloud API** — API ufficiale Meta.
> Riceve webhook per **ogni messaggio** in arrivo sul numero business.
> Soluzione ufficiale, nessun rischio ban, nessuna libreria non ufficiale.

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `WHATSAPP_ACCESS_TOKEN` | Meta for Developers → App → WhatsApp → API Setup | Token di sistema permanente (non scade) — generare da Business Manager |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta for Developers → App → WhatsApp → API Setup | ID del numero business registrato |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | Meta Business Manager → Account info | |
| `WHATSAPP_VERIFY_TOKEN` | Scelto da noi | Stringa arbitraria per la verifica del webhook |
| URL webhook pubblico HTTPS | Nostro server | Meta chiama questo endpoint ad ogni messaggio ricevuto |
| App Meta in modalità Live | Meta App Review | In Development riceve solo messaggi da numeri di test |
| Permesso `messages` sul webhook | Meta for Developers → App → Webhooks | Selezionare il campo `messages` nella subscription |

---

## 2. Telegram (via Telethon / Pyrogram — MTProto client)

> Approccio: **Telethon** o **Pyrogram** si connettono come client utente tramite l'API MTProto ufficiale di Telegram.
> Legge **tutti** i messaggi ricevuti — chat private, gruppi, canali, chat di bot.
> Completamente legale, API ufficiale Telegram. Nessun bot necessario.

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `TELEGRAM_API_ID` | my.telegram.org → API development tools | Registrare una app Telegram |
| `TELEGRAM_API_HASH` | my.telegram.org → API development tools | Stessa schermata dell'API ID |
| Numero di telefono dell'account | — | Il numero Telegram da monitorare |
| Codice OTP | Inviato da Telegram al numero | Richiesto al primo avvio per autenticare la sessione |
| `TELEGRAM_SESSION_NAME` | Scelto da noi | Nome file `.session` generato da Telethon/Pyrogram |
| Sessione persistente | Generata automaticamente | Evita re-autenticazione ad ogni riavvio |
| 2FA password (se attiva) | — | Password di verifica in due passaggi dell'account |

---

## 3. Email (caselle IMAP/SMTP)

Per ogni casella email:

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `EMAIL_n_HOST` | Provider email (es. imap.gmail.com) | |
| `EMAIL_n_PORT` | Provider email | IMAP: 993, SMTP: 587 |
| `EMAIL_n_USER` | Indirizzo email | |
| `EMAIL_n_PASSWORD` | Account email | Per Gmail usare App Password, non password principale |
| `EMAIL_n_USE_SSL` | — | True/False |
| App Password Gmail | Google Account → Sicurezza → Password app | Solo se 2FA attivo |
| `EMAIL_n_FOLDER` | — | Es. INBOX, Sent, ecc. |

> Replicare le variabili per ogni casella aggiuntiva (EMAIL_1_, EMAIL_2_, ...).

---

## 4. Microsoft Teams

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `AZURE_TENANT_ID` | Azure Portal → Azure Active Directory → Overview | |
| `AZURE_CLIENT_ID` | Azure Portal → App registrations → App → Overview | |
| `AZURE_CLIENT_SECRET` | Azure Portal → App registrations → App → Certificates & secrets | |
| Permessi Microsoft Graph | Azure Portal → App registrations → API permissions | Richiede: `ChannelMessage.Read.All`, `Chat.Read`, `Team.ReadBasic.All` |
| Consenso amministratore | Admin Azure AD | Per permessi applicativi (non delegati) |
| `TEAMS_WEBHOOK_URL` | Nostro server HTTPS | Opzionale, se si usano subscription Graph |

---

## 5. ClickUp


| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `CLICKUP_API_TOKEN` | ClickUp → Impostazioni → Apps → API Token | Token personale o OAuth |
| `CLICKUP_TEAM_ID` | ClickUp API → /team | ID del workspace |
| Lista Space/Folder/List ID da monitorare | ClickUp API → /team/{id}/space | Configurare quali spazi sincronizzare |
| `CLICKUP_WEBHOOK_SECRET` | Generato da noi | Per verifica firma webhook |
| URL webhook pubblico | Nostro server HTTPS | Registrabile via API ClickUp |

---

## 6. SMS

> Opzione A: **Twilio** — SIM virtuale cloud, webhook per ogni SMS ricevuto.
> Opzione B: **SIM locale** tramite dispositivo Android + app bridge (es. SMS Gateway, SmsToHttp).

### Opzione A — Twilio (consigliato)

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `TWILIO_ACCOUNT_SID` | console.twilio.com → Account info | |
| `TWILIO_AUTH_TOKEN` | console.twilio.com → Account info | |
| `TWILIO_PHONE_NUMBER` | console.twilio.com → Phone Numbers | Numero acquistato su Twilio |
| URL webhook HTTPS | Nostro server | Configurare in Twilio → Phone Number → Messaging webhook |

### Opzione B — SIM locale (Android bridge)

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| App bridge SMS→HTTP | Es. `SMS Gateway for Android` | Installa sul telefono con la SIM |
| `SMS_BRIDGE_SECRET` | Scelto da noi | Token per autenticare le chiamate dal bridge |
| URL webhook HTTPS | Nostro server | Il telefono chiama questo endpoint per ogni SMS |

---

## 7. GitHub

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `GITHUB_WEBHOOK_SECRET` | Scelto da noi | Configurato nel repository → Settings → Webhooks |
| URL webhook HTTPS | Nostro server | GitHub chiama questo endpoint per ogni evento |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens | Per leggere dati via REST API (issue, PR, ecc.) |
| Lista repo da monitorare | — | Configurare il webhook su ogni repo o a livello org |
| Eventi webhook da abilitare | GitHub → Webhook → Events | Selezionare: `issues`, `pull_request`, `issue_comment`, `push`, `release` |

---

## 8. Google Drive

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| API Google Drive abilitata | Google Cloud Console → APIs & Services → Enable APIs | Abilitare: Drive API v3 |
| Scopes aggiuntivi OAuth | — | Aggiungere `https://www.googleapis.com/auth/drive.readonly` agli scopes esistenti |
| `DRIVE_FOLDER_IDS` | Google Drive → cartella → tasto destro → Ottieni link | ID delle cartelle da monitorare |
| Push notifications (opzionale) | Google Drive API → channels.watch | Alternativa al polling — notifica webhook ad ogni modifica |

---

## 9. Home Assistant

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `HA_BASE_URL` | Indirizzo locale o remoto dell'istanza Home Assistant | Es. `http://homeassistant.local:8123` |
| `HA_TOKEN` | Home Assistant → Profilo → Long-Lived Access Tokens | Token di accesso a lungo termine |
| Lista entità/eventi da monitorare | Home Assistant → Developer Tools → States | ID delle entità di interesse |
| Webhook HA → big-sync (opzionale) | Home Assistant → Automazioni | HA può chiamare big-sync via webhook su eventi specifici |

---

## 10. RSS Feed

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `RSS_FEED_URLS` | — | Lista di URL feed RSS/Atom da monitorare |
| `RSS_POLL_INTERVAL` | Scelto da noi | Intervallo di polling in minuti (es. 30) |
| Nessun token richiesto | — | Feed pubblici non richiedono autenticazione |
| `RSS_AUTH_FEEDS` | — | Feed privati con Basic Auth o token — configurare per-feed |

---

## 11. Google Gemini API

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `GEMINI_API_KEY` | Google AI Studio → Get API Key | Unica chiave per tutti i 5 workflow |
| Quota/piano attivo | Google AI Studio o Google Cloud | Verificare rate limit per volume atteso |

---

## 12. Google Workspace (Contacts, Calendar, Tasks)

| Cosa | Dove si ottiene | Note |
|------|----------------|-------|
| `GOOGLE_CLIENT_ID` | Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 | |
| `GOOGLE_CLIENT_SECRET` | Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 | |
| `GOOGLE_REDIRECT_URI` | Nostro server | Es. https://dominio/auth/google/callback |
| `GOOGLE_REFRESH_TOKEN` | Flusso OAuth completato almeno una volta | Salvare il refresh token, non scade |
| API abilitate | Google Cloud Console → APIs & Services → Enable APIs | Abilitare: People API, Calendar API, Tasks API |
| OAuth consent screen configurato | Google Cloud Console → APIs & Services → OAuth consent screen | Aggiungere scopes: contacts, calendar, tasks |
| Scopes richiesti | — | `https://www.googleapis.com/auth/contacts`, `https://www.googleapis.com/auth/calendar`, `https://www.googleapis.com/auth/tasks` |

---

## 13. Infrastruttura

| Cosa | Note |
|------|-------|
| Server con IP pubblico e HTTPS | Necessario per i webhook di Meta, Telegram, Graph, ClickUp |
| Certificato SSL valido | Let's Encrypt o equivalente |
| `SECRET_KEY` Django | Generare con `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DATABASE_URL` | PostgreSQL consigliato per produzione |
| Variabili d'ambiente in `.env` | Non committare mai credenziali nel repo |

---

## Riepilogo file .env necessario

```env
# Django
SECRET_KEY=
DEBUG=False
ALLOWED_HOSTS=

# Database
DATABASE_URL=

# WhatsApp Business (Cloud API)
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_VERIFY_TOKEN=

# Telegram (Telethon/Pyrogram MTProto)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_NAME=big_sync_telegram

# Email (ripetere per ogni casella)
EMAIL_1_HOST=
EMAIL_1_PORT=
EMAIL_1_USER=
EMAIL_1_PASSWORD=

# Microsoft Teams
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=

# ClickUp
CLICKUP_API_TOKEN=
CLICKUP_TEAM_ID=
CLICKUP_WEBHOOK_SECRET=

# SMS (Twilio)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# GitHub
GITHUB_WEBHOOK_SECRET=
GITHUB_TOKEN=

# Google Drive
DRIVE_FOLDER_IDS=

# Home Assistant
HA_BASE_URL=
HA_TOKEN=

# RSS
RSS_FEED_URLS=
RSS_POLL_INTERVAL=30

# Gemini
GEMINI_API_KEY=

# Google Workspace
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
GOOGLE_REFRESH_TOKEN=
```
