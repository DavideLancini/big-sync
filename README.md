# big-sync

Aggregatore personale di messaggi, audio e notizie con automazione AI. Le fonti vengono ingerite localmente, analizzate da Gemini e materializzate come contatti, eventi calendario e todo su Google Workspace dell'utente. Una dashboard web mostra lo stato di ogni flusso e permette di lanciare ogni operazione manualmente.

Questo documento descrive **cosa fa l'applicazione**, sezione per sezione, modello per modello. Per le credenziali vedere `CREDENTIALS.md` (non versionato).

---

## 1. Panoramica

### Cosa fa, in concreto

1. **Ingerisce** messaggi e contenuti da fonti esterne (Telegram, WhatsApp, Gmail, RSS, registratore audio Plaud).
2. **Salva tutto in DB** (PostgreSQL in produzione, SQLite in locale) — i messaggi non vengono mai mostrati direttamente all'utente, sono il dato grezzo su cui ragiona l'AI.
3. **Trascrive gli audio** (Telegram voice, WhatsApp audio, Plaud) usando Gemini File API.
4. **Analizza ogni messaggio** con un workflow Gemini dedicato che estrae JSON strutturato `{contacts, events, todos}`.
5. **Scrive su Google Workspace** con dedupe difensiva: i contatti finiscono in Google Contacts (rubrica primaria), gli eventi e i todo finiscono in Google Calendar. Le note lunghe sui contatti finiscono in Google Drive con link nel campo notes.
6. **Mostra una dashboard** con KPI per fonte, stato delle pipeline, log delle ultime attività e pulsanti per ri-lanciare manualmente i comandi più rilevanti.

### Fonti attive vs. placeholder

| Fonte | Stato | Pipeline |
|-------|-------|----------|
| **Telegram** | Live | Listener daemon (Telethon) + analyzer batch |
| **WhatsApp** | Live | Listener daemon (neonize) + analyzer batch |
| **Plaud** (registratore audio) | Live | Cron sync da cloud Plaud + trascrizione + riassunto |
| **Email / Gmail** | Live | Cron import + analyzer (tagging) |
| **RSS** | Live | Cron fetch + classifier + summarizer + briefing audio |
| Microsoft Teams | Placeholder | Solo nav stub |
| ClickUp | Placeholder | Solo nav stub |
| SMS | Placeholder | Solo nav stub |
| GitHub | Placeholder | Solo nav stub |
| Google Drive (come fonte) | Placeholder | Solo nav stub (Drive è usato come *output* per le note contatti) |
| Home Assistant | Placeholder | Solo nav stub |

---

## 2. Stack tecnico

- **Framework**: Django 6, Python 3.13.
- **Database**: PostgreSQL in produzione, SQLite in dev. Schema gestito via migrations Django.
- **AI**: Google Gemini (`gemini-2.5-flash` per testo e File API audio, `gemini-2.5-flash-preview-tts` per il briefing audio).
- **Output API**: Google People API (Contacts), Google Calendar API, Google Drive API.
- **Web server prod**: gunicorn dietro nginx, autenticazione single-session a password.
- **Background jobs**: tutti via systemd (cinque timer + due daemon long-running, dettaglio in §11). Nessun cron tradizionale, niente Celery.
- **Frontend dashboard**: HTML statico + piccole isole di JavaScript vanilla. SSE (`Server-Sent Events`) per streamare l'output dei comandi lanciati dalla UI.

---

## 3. Struttura del progetto

```
big-sync/
├── config/                 # settings.py, urls.py, wsgi.py
├── common/                 # Auth, billing, dashboard, modelli condivisi (Contact, WriteLog)
├── workflows/              # Pipeline AI (gemini.py, prompts.py, tts.py, workflow_*.py)
├── outputs/                # Scrittura su Google Workspace (contacts, calendar, todos, drive)
├── sources/                # Una app Django per fonte
│   ├── telegram/           ├── whatsapp/   ├── plaud/
│   ├── email_source/       ├── rss/        ├── teams/
│   ├── clickup/            ├── sms/        ├── github/
│   ├── drive/              ├── home_assistant/
├── scripts/                # Setup oneshot (oauth, fix migrazioni)
├── media/                  # File scaricati (audio Plaud, voice Telegram, allegati WA) — gitignored
└── manage.py
```

Ogni app sotto `sources/` è autosufficiente: ha i propri model, le proprie migrations, i propri management commands. Le sei app placeholder (Teams, ClickUp, SMS, GitHub, Drive, Home Assistant) contengono solo lo scheletro Django (apps.py, models.py vuoto) per riservare il namespace.

---

## 4. App `common` — fondazioni condivise

### `common/models.py`

| Modello | Cosa contiene |
|---------|---------------|
| `Contact` | Cache locale dei contatti Google. Campi: `resource_name` (id People API), `name`, `phones` (JSON list di stringhe normalizzate), `emails` (JSON list lowercase), `company`, `role`, `notes` (testo libero), `notes_url` (link Drive quando le note crescono troppo). Usato per dedupe veloce senza sbattere ogni volta sull'API People. |
| `WriteLog` | Log immutable di ogni scrittura effettuata su Google Workspace. Campi: `type` (`contact`/`event`/`task`), `title`, `detail`, `created_at`. Alimenta la sezione "Attività recente" della home. |
| `ContactsSyncLog` | Timestamp + count dell'ultima full sync rubrica → DB locale. |
| `ActiveSession` | Singolo record che memorizza la session key dell'unico utente loggato in dashboard. Serve a **invalidare la sessione precedente** quando qualcuno fa login da un altro device. |

### `common/google_auth.py`

`get_credentials()` costruisce credenziali OAuth riutilizzabili da `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN*`. L'app supporta tre refresh token separati per Workspace (`GOOGLE_REFRESH_TOKEN`, `GOOGLE_REFRESH_TOKEN_CONTACTS`, `GOOGLE_REFRESH_TOKEN_CALENDAR`) perché Google emette token con scope diversi.

### `common/google_billing.py`

Wrapper sull'API Cloud Billing Budgets. `billing_summary()` ritorna spesa attuale + budget configurato, mostrato come card nella home. Usa l'access token dell'OAuth Workspace.

### `common/management/commands/`

- **`sync_contacts`** — full reload della rubrica Google → tabella `Contact`. Normalizza i numeri di telefono (solo cifre) e le email (lowercase) per garantire dedupe deterministica.
- **`migrate_notes_to_drive`** — comando one-shot: per i `Contact` con campo `notes` lungo, sposta le note in un Google Doc dedicato e popola `notes_url`.

### `common/views.py` — la dashboard web

Tutte le view sono protette da `_is_authenticated()` che valida `request.session[_SESSION_KEY] is True` E `session_key == ActiveSession.get_current_key()` (single-session enforcement).

| URL | View | Cosa mostra |
|-----|------|-------------|
| `/` | `home` | Skeleton minimal — i KPI vengono caricati async via `/api/home-stats/` per non bloccare il rendering su chiamate Gemini/Billing. |
| `/api/home-stats/` | `home_stats_json` | JSON: stato Gemini (test ping), billing budget, conteggi contatti/eventi/task/contatti scritti, totali per fonte (telegram/whatsapp/rss/plaud), 10 ultime attività. |
| `/telegram/` | `telegram_dashboard` | KPI messaggi totali/analizzati/pending, ultimo messaggio, ultime entità create. Pulsanti per import storia + analisi. |
| `/whatsapp/` | `whatsapp_dashboard` | Stesso pattern di Telegram. Banner promemoria che ricorda di rifare il pairing del device entro una data per recuperare lo storico (limite del protocollo WhatsApp multi-device). |
| `/plaud/` | `plaud_dashboard` | Lista delle 50 registrazioni recenti con titolo AI, sommario AI, badge stato (trascritto/riassunto/errore). Quattro pulsanti azione: Sync, Carica, Trascrivi, Riassumi. |
| `/plaud/upload/` | `plaud_upload` | POST multipart per upload manuale di file audio (fallback se il sync cloud non funziona). |
| `/email/` | `email_dashboard` | Lista delle ultime 200 email con tag, filtro per tag laterale. |
| `/email/<gmail_id>/` | `email_detail` | Vista singola email con corpo completo. |
| `/rss/` | `rss_dashboard` | Due tab: "Notizie" (lista articoli) e "Riassunti" (sommario giornaliero per topic). Selettore data per navigare lo storico riassunti. Player audio del briefing del giorno. |
| `/rss/<pk>/` | `rss_article` | Singolo articolo, marca come letto. |
| `/rss/audio/<YYYY-MM-DD>/` | `rss_audio` | Streaming WAV del briefing audio. |
| `/teams/`, `/clickup/`, `/sms/`, `/github/`, `/gdrive/`, `/homeassistant/` | `source_placeholder` | Pagina placeholder "coming soon". |
| `/login/`, `/logout/` | `login_view`, `logout_view` | Login a password (`DASHBOARD_PASSWORD`). Il login fa `cycle_key()` e aggiorna `ActiveSession` → kick di ogni altra sessione attiva. |
| `/run/<action>/` | `run_command` | Esegue uno dei management commands ammessi (whitelist hardcoded) e streamma stdout via SSE. Usato dai pulsanti nelle dashboard. |
| `/admin/` | Django admin | Standard. |

### Template `common/templates/common/`

- `_sidebar.html` — barra laterale con icone svg per tutte le fonti.
- `home.html` — overview, KPI async-load.
- `telegram.html`, `whatsapp.html`, `plaud.html`, `email.html`, `email_detail.html`, `rss.html`, `rss_article.html` — dashboard per fonte.
- `login.html`, `placeholder.html`, `dashboard.html` — utility.

Tutti i template condividono lo stesso CSS dark inline (palette `#0f1117` background, `#6366f1` accent indigo, badge colorati per stati).

---

## 5. App `sources/telegram`

### `sources/telegram/models.py`

`TelegramMessage` — un record per messaggio. Chiave naturale `(chat_id, message_id)`. Campi:

- `chat_id` (BigInteger, index), `chat_name`
- `sender_id`, `sender_name`
- `text`
- `media_type`: `text` / `photo` / `video` / `voice` / `audio` / `video_note` / `document` / `sticker` / `location` / `contact` / `unknown`
- `media_downloaded`, `media_path` — file scaricato sotto `media/telegram/<slug>/<id>.<ext>`
- `transcription` — testo Gemini per i media audio
- `date` — timestamp del messaggio
- `raw` — dump JSON dell'evento Telethon
- `processed` — flag analyzer (`process_realtime_message` setta a True)
- `created_at`

`MediaType` — `TextChoices` con tutti i tipi supportati.

### `sources/telegram/media.py`

Helpers per:
- Decidere se ignorare una chat / entity (es. canali pubblici grossi tramite `TELEGRAM_IGNORE_CHATS`).
- Detect del media type da un messaggio Telethon.
- Slugify nome chat → directory `media/telegram/<slug>/`.
- Serializzazione raw event a JSON.

### `sources/telegram/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `telegram_auth` | One-shot interattivo: prompt per `TELEGRAM_API_ID`/`HASH`, login con codice OTP, salva file di sessione (`big_sync_telegram.session`). Da rifare solo se il file di sessione viene perso. |
| `telegram_listener` | **Daemon long-running** (systemd). Si connette via Telethon, riceve eventi `NewMessage` real-time, salva su DB, scarica eventuali media, trascrive audio, lancia `process_realtime_message` con 10 messaggi di contesto. Mirror del whatsapp_listener. |
| `telegram_import_history` | Importa lo storico chat per chat. Modalità: backfill in avanti (`oldest_message_id` → ora) oppure indietro (storia precedente). Flag `--gap-check` riempie buchi. Rispetta `TELEGRAM_IGNORE_CHATS`. |
| `telegram_analyze_history` | Analizza messaggi non processati in batch giorno-per-chat. Per audio non ancora trascritti chiama `transcribe_audio`. Flag `--one-chat` / `--from` / `--to`. |
| `telegram_backfill_todos` | Comando di **migrazione storica** che trasforma vecchi todo Google Tasks in eventi calendario `[todo] <titolo>` con start time e durata 30 min. Trova slot liberi 08:00-23:00, overflow su giorno successivo cap a oggi. |

### `sources/telegram/ignored.py`

Lista hard-coded di chat ID da ignorare in fase di import (overlap con `TELEGRAM_IGNORE_CHATS`, qui per casi più strutturali).

---

## 6. App `sources/whatsapp`

Costruita per **comportarsi come `telegram`** — stesso flusso listener+analyzer, stessi modelli, stessa dashboard.

### `sources/whatsapp/models.py`

`WhatsAppMessage` — chiave naturale `(chat_jid, message_id)`. Identico a `TelegramMessage` ma usa stringhe JID (`user@s.whatsapp.net`, `groupid@g.us`) invece di interi. Aggiunge:

- `chat_jid`, `sender_jid` (CharField perché JID sono stringhe)
- `is_from_me` — escluso dall'analisi per non eco
- `is_group` — usato per cercare il `chat_name` nel group info

`WaMediaType` — `TextChoices` con `text` / `photo` / `video` / `voice` / `audio` / `sticker` / `gif` / `document` / `location` / `contact` / `unknown`.

### `sources/whatsapp/parse.py`

Convertitori da messaggi protobuf neonize → dict Python:
- `jid_str(jid)` → `"user@server"`
- `detect_media_type(msg)` → controlla `imageMessage.URL`, `audioMessage.URL` (con `.PTT` → voice vs audio), ecc.
- `message_text(msg)` — best-effort per estrarre testo da conversation, extendedTextMessage o caption media.
- `parse_event(event)` — flatten completo dell'evento, gestisce timestamp ms vs s con detect su magnitudine.

### `sources/whatsapp/media.py`

`download_media(client, event, chat_name, media_type)` — scarica il media nella cartella per chat (`media/whatsapp/<slug>/`), restituisce il path. Mappa estensioni per type.

### `sources/whatsapp/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `whatsapp_pair` | One-shot: pairing iniziale del device WhatsApp tramite `WHATSAPP_PHONE`. Genera codice da inserire sull'app del telefono. **Legacy** — il listener stesso ora gestisce il pair se manca il device, mantenuto per troubleshooting. |
| `whatsapp_listener` | **Daemon long-running** (systemd). Connessione neonize a whatsmeow (Go), un solo processo che gestisce sia pair (se necessario) che listener real-time. Eventi: `MessageEv` → ingest singolo messaggio + analisi. `HistorySyncEv` → ingest dump storico (push una tantum al pairing iniziale, ~30-90 giorni). `ConnectedEv`, `PairStatusEv` → log. |
| `whatsapp_analyze_history` | Analizza messaggi pending in batch. Mirror di `telegram_analyze_history`. |

**Importante**: WhatsApp multi-device permette il dump dello storico **una sola volta**, al pairing. Dopo, solo i messaggi nuovi arrivano in real-time. Per recuperare lo storico dopo perdita serve **rifare il pairing**, dopo aver cancellato `whatsapp_session.sqlite3`. La dashboard mostra un banner promemoria con la data scelta.

---

## 7. App `sources/plaud`

Integrazione con il registratore vocale **Plaud Note**, senza l'abbonamento del produttore. Plaud carica le registrazioni nel suo cloud automaticamente: noi le scarichiamo via API reverse-engineered, le trascriviamo con Gemini, generiamo titolo+sommario, e estraiamo entità.

### `sources/plaud/models.py`

`PlaudRecording`:

- `plaud_id` (CharField unique, dedupe naturale)
- `file` (FileField → `media/plaud/`)
- `original_name`, `size_bytes`, `duration_ms`, `serial_number`
- `transcription` — testo grezzo della trascrizione Gemini
- `title` (max 80 char), `summary` (markdown)
- `processed` — True quando trascritto
- `summarized` — True quando ha titolo + sommario
- `error` — ultimo messaggio d'errore della pipeline
- `recorded_at` (UTC), `created_at`
- Ordering default: `recorded_at` desc.

### `sources/plaud/client.py`

Client Python minimale per la API Plaud (porting da `rsteckler/applaud`). Endpoints:

- `GET /file/simple/web?skip=0&limit=50&is_trash=2&sort_by=start_time&is_desc=true` — lista registrazioni
- `GET /file/temp-url/{id}` — pre-signed URL S3 per scaricare l'audio

Auth: header `Authorization: Bearer <PLAUD_TOKEN>`. Il token è un JWT estratto da `web.plaud.ai` localStorage chiave `tokenstr` — vale circa 10 mesi.

Region: l'API ha tre endpoint regionali (`us-west-2`, `eu-central-1`, `ap-southeast-1`). Se chiami quello sbagliato l'API risponde HTTP 200 con `status:-302` e ti dice il dominio corretto: il client logga la correzione e fa retry una volta. Region salvata in `PLAUD_REGION`.

Gestione errori: 401 → `PlaudAuthError` (token scaduto). 5xx → 3 retry exponential backoff. Network error → 3 retry.

### `sources/plaud/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `plaud_sync` | Lista cloud → dedupe contro `plaud_id` esistenti → scarica i nuovi nello stream (`requests.get(url, stream=True)`) → salva in `media/plaud/{plaud_id}.{ext}` → crea record `PlaudRecording`. |
| `plaud_process_pending` | Per ogni registrazione `processed=False`: chiama `transcribe_audio(path, return_usage=True)`, salva `transcription`, marca `processed=True`. Logga token usage per registrazione e totale di sessione. |
| `plaud_summarize_pending` | Per ogni registrazione con `transcription` non vuota e `summarized=False`: chiama `summarize_transcription(text)` → `(title, summary)`, salva, marca. Poi chiama `process_realtime_message("Plaud · Voice Notes", new_msg, [])` sulla trascrizione → estrae contacts/events/todos. |

I tre comandi sono concatenati in `big-sync-plaud.service` (timer ogni 10 min): sync → trascrivi → riassumi.

---

## 8. App `sources/email_source` (Gmail)

### `sources/email_source/models.py`

| Modello | Cosa contiene |
|---------|---------------|
| `EmailTag` | Tag con `name`, `color`, `gmail_label_id` (id label Gmail corrispondente). Tag predefiniti seedati a runtime. |
| `GmailMessage` | `gmail_id`, `thread_id`, `subject`, `sender`, `sender_email`, `snippet`, `body_text`, `date`, `gmail_labels` (lista raw label dal Gmail API), `tags` (M2M `EmailTag`), `analyzed`, `created_at`. |
| `GmailSyncState` | Singleton (`id=1`) che memorizza l'ultimo `history_id` Gmail per fare incremental sync. |

### `sources/email_source/gmail_client.py`

Wrapper sulla Gmail API:
- `get_service()` → service Gmail v1 con OAuth.
- `parse_message(msg)` → estrae sender (nome+email), date, body (text plain priorità), labels.
- `get_or_create_gmail_label(service, tag_name)` → crea label Gmail se non esiste, ritorna id.
- `apply_labels_to_message(...)` → applica lista label id a una mail.

### `sources/email_source/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `gmail_import` | Modalità `--full` (prima esecuzione): scarica tutte le mail dell'inbox via list+get, salva su DB. Memorizza `history_id` per i futuri incremental. Modalità default: incremental — usa `users.history.list(startHistoryId=...)` e ingerisce solo i delta. |
| `gmail_sync` | Wrapper "smart": se `GmailSyncState.history_id` esiste fa incremental, altrimenti full. Chiamato dal timer systemd ogni 30 min. Restituisce conteggi per logging. |
| `gmail_analyze` | Per ogni `GmailMessage` con `analyzed=False`: chiama `tag_email(sender, subject, body)` (Gemini) → ottiene lista tag suggeriti, crea/recupera `EmailTag`, applica come label Gmail, marca `analyzed=True`. Locking via `_claim()` per supportare run paralleli. |

I tag predefiniti sono seedati alla prima esecuzione di `gmail_analyze` con `_seed_tags()`. La lista tag suggeribili è hardcoded nel prompt.

---

## 9. App `sources/rss`

Aggregatore RSS con **classificazione per topic** e **briefing audio giornaliero**.

### `sources/rss/models.py`

| Modello | Cosa contiene |
|---------|---------------|
| `RssFeed` | `name`, `url` (unique), `active`, `last_fetched`, `created_at`. Lista feed seedata da `_seed_feeds()`. |
| `RssArticle` | `feed` (FK), `guid` (unique, dedupe), `title`, `url`, `summary`, `content` (full text scaricato), `published_at`, `read`, `analyzed`, `created_at`. |
| `RssTopic` | `slug` (unique), `name`, `order`. Topic predefiniti: tech, AI, business, mondo, ecc. Seedati da `_seed_topics()`. |
| `RssDailySummary` | `topic` (FK), `date`, `text` (markdown sommario del giorno), `article_count`. Una riga per topic per giorno. |

### `sources/rss/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `rss_fetch` | Per ogni `RssFeed.active=True`: parsea il feed con `feedparser`, dedupe per `guid`, scarica full content (segue link articolo), salva nuovi `RssArticle`. Aggiorna `last_fetched`. |
| `rss_analyze` | Per ogni `RssArticle.analyzed=False`: chiama `classify_article(title, text)` → topic slug, poi `merge_into_summary(topic, current_summary, title, source, text)` → estende il `RssDailySummary` del giorno con il nuovo articolo. Locking via `_claim_article()`. Cleanup periodico di articoli vecchi non interessanti. |
| `rss_update` | Wrapper: lancia `rss_fetch` poi `rss_analyze`. Chiamato dal timer ogni 5 min. |
| `rss_audio_generate` | Genera il briefing audio del giorno (o di `--date`). Concatena i sommari di tutti i topic ordinati per `RssTopic.order`, li passa a `workflows/tts.py:generate_daily_briefing` → file WAV in `media/rss_audio/<YYYY-MM-DD>.wav`. Servito dalla dashboard. |

---

## 10. Workflow AI (`workflows/`)

### `workflows/gemini.py` — primitive

Tutto passa da qui. Funzioni:

- `_get_client()` — singleton `genai.Client` con HTTP timeout 180s.
- `transcribe_audio(file_path, model="gemini-2.5-flash", retries=4, return_usage=False)` — upload file via File API, poll `_wait_for_active()` (max 120s, intervallo 2s) finché stato = ACTIVE, poi `generate_content` con prompt italiano "trascrivi parola per parola". Retry 5xx con backoff. Cleanup file remoto sempre. Se `return_usage=True` ritorna tupla `(text, {prompt, output, total})` usando `usage_metadata` della response.
- `summarize_transcription(text)` → `(title, summary)`. Prompt italiano che chiede formato delimitato `TITLE: ...\n---SUMMARY---\n<markdown>`. Parser robusto a fallback (prima riga = title se manca delimiter). Non usa JSON perché il summary contiene markdown con virgolette/newline che rompono il parsing.
- `extract_json(text)` — tollera fence ```` ```json ````, trova il primo `{`/ultimo `}`, ritorna `{contacts:[], events:[], todos:[]}` su parse fail.
- `ask_text(prompt)` — generate_content, ritorna stringa, retry 5xx.
- `ask(prompt)` — come sopra ma fa `extract_json` sulla response.

### `workflows/prompts.py` — prompt builder

- `batch_prompt(chat_name, date, messages)` — analisi di un giorno intero di una chat. Estrai contacts/events/todos.
- `single_prompt(chat_name, sender, datetime, text, media_type)` — singolo messaggio offline (storia).
- `realtime_prompt(chat_name, new_msg, context_msgs)` — usato dal listener: estrai entità solo da `new_msg`, ma usa `context_msgs` (10 ultimi) per disambiguare riferimenti tipo "vediamoci giovedì alle 15".

Tutti i prompt enforce schema JSON `{contacts, events, todos}` con campi specifici (es. `events` deve avere `start_date`, `start_time`, `duration_minutes` obbligatori — fallback al datetime del messaggio se l'AI non li specifica).

### `workflows/workflow_telegram.py`

Pipeline analisi messaggi (usato anche da WhatsApp e Plaud, il nome è storico).

- `process_batch(chat_name, date, messages)` — chiama `batch_prompt`, parsa, scrive estrazioni.
- `process_message(chat_name, sender, datetime, text, media_type)` — singolo offline.
- `process_realtime_message(chat_name, new_msg, context_msgs)` — singolo realtime, l'entry-point usato da listener Telegram, listener WhatsApp e Plaud summarize.
- `_write_extracted(extracted, source, fallback_datetime)` — itera su `extracted["contacts"]`, `["events"]`, `["todos"]` e chiama `upsert_contact`, `upsert_event`, `upsert_todo_event`. Conta successi e ritorna `{contacts, events, todos}`. Cattura eccezioni per item — un fallimento non blocca gli altri.

### `workflows/workflow_email.py`

`tag_email(sender, subject, body)` — Gemini riceve mittente + oggetto + corpo, ritorna lista di tag ammessi. Lista tag hardcoded nel prompt (es. Personale, Notifiche, Lavoro, Spam, Urgente, ...).

### `workflows/workflow_rss.py`

- `classify_article(title, text)` → slug topic.
- `merge_into_summary(topic_name, current_summary, title, source, text)` → nuovo testo del sommario. Gemini riceve il sommario corrente e l'articolo nuovo, restituisce sommario aggiornato che integra le info nuove senza duplicazioni.

### `workflows/tts.py`

Briefing audio quotidiano. Voce `Charon`, sample rate 24kHz.

- `_text_to_pcm(text)` — chiamata a `gemini-2.5-flash-preview-tts` con `response_modalities=["AUDIO"]`. Ritorna bytes PCM raw.
- `_pcm_to_wav(pcm)` — wraps in container WAV.
- `generate_daily_briefing(date_label, summaries)` — itera sui topic, sintetizza ogni sommario, concatena con 0.6s di silenzio tra sezioni.

---

## 11. Outputs (scritture su Google Workspace)

### `outputs/contacts.py`

`upsert_contact(data)`:
1. Normalizza phone (solo cifre) e email (lowercase) dalla `data`.
2. `_find_existing_local` cerca `Contact` locale per nome esatto, oppure intersezione phone, oppure intersezione email.
3. Se non esiste → crea contatto via People API + record locale, ritorna `resource_name`.
4. Se esiste → enrichment: aggiunge solo campi nuovi (`role`, `company`, phone/email mancanti), ritorna l'id esistente.
5. Se le note crescono troppo (> 500 chars) le sposta in un Google Doc dedicato e mette il link in `notes_url`.

### `outputs/calendar.py`

`upsert_event(data)`:
1. Cerca eventi esistenti su Calendar primary per `summary` + `date`.
2. Se overlap con eventi esistenti → enrichment (aggiunge link meet, descrizione, partecipanti).
3. Se nuovo → crea con `_build_body(data)` (start/end RFC3339, attendees, description).
4. Logga in `WriteLog` come `event`.

### `outputs/todos.py`

I todo sono **eventi calendario con prefisso `[todo] `**, non Google Tasks (che non supporta start time). Pipeline:

`upsert_todo_event(data, fallback_datetime)`:
1. `_parse_start(date, time)` — parsa la data dell'AI, fallback al datetime del messaggio sorgente se mancante.
2. `_find_existing` per titolo normalizzato + start.
3. Se esiste → noop. Altrimenti crea evento `[todo] <titolo>` di 15 min default.

`find_free_slot(service, day, duration_min, start_hour=8, end_hour=23, extra_busy=None)` — usato dal backfill: trova il primo slot libero scansionando eventi esistenti. Ignora eventi all-day (altrimenti bloccherebbero l'intera giornata).

`create_todo_event_at(service, title, start, duration_min, notes)` — usato da `telegram_backfill_todos` per posizionare todo storici in slot calendario.

### `outputs/tasks.py` (legacy)

`upsert_task(data)` — usa Google Tasks API. **Deprecato** dopo lo switch a `upsert_todo_event`. Mantenuto per riferimento, non usato dai nuovi workflow.

### `outputs/drive.py`

`append_contact_note(contact_name, full_notes, notes_url)`:
1. Se `notes_url` esiste → appendi a quel Doc.
2. Altrimenti crea Doc nella cartella big-sync (`_get_or_create_folder`), ritorna URL.

---

## 12. Background services & cron

Tutto su systemd (server). Niente cron tradizionale.

### Daemon long-running (uno per fonte real-time)

| Service | Comando |
|---------|---------|
| `big-sync.service` | gunicorn — il web (dashboard + admin). |
| `big-sync-telegram.service` | `manage.py telegram_listener` — Telethon connection persistente. Restart on failure. |
| `big-sync-whatsapp.service` | `manage.py whatsapp_listener` — neonize connection persistente. Restart on failure. Anche pair iniziale. |

### Timer (cron systemd)

| Timer | Frequenza | Service che lancia |
|-------|-----------|--------------------|
| `big-sync-rss-update.timer` | 5 min | `rss_update` (fetch + analyze) |
| `big-sync-gmail-sync.timer` | 30 min | `gmail_sync` (incremental import) |
| `big-sync-plaud.timer` | 10 min | concatena `plaud_sync` → `plaud_process_pending --limit 50` → `plaud_summarize_pending --limit 50` |

Note:
- `gmail_analyze` non ha un timer dedicato — viene chiamato manualmente dalla UI o eseguito on-demand.
- I listener Telegram/WhatsApp gestiscono **anche** l'analisi real-time inline (chiamano `process_realtime_message` per ogni messaggio nuovo). I comandi `*_analyze_history` servono solo per backfill manuale.

---

## 13. Flussi end-to-end (esempi)

### Esempio 1: messaggio Telegram con un appuntamento

1. Listener Telethon riceve `NewMessage` da chat "Marco Rossi", testo "ci vediamo domani alle 15 in ufficio".
2. `_save_message` → record `TelegramMessage`.
3. `_get_context_messages(chat_id, limit=10)` → ultimi 10 messaggi della chat per contesto.
4. `_analyze_new_message` → `process_realtime_message("Marco Rossi", new_msg, context)`.
5. `realtime_prompt(...)` → Gemini → JSON `{contacts: [], events: [{summary:"Incontro con Marco Rossi", start_date:"2026-05-13", start_time:"15:00", duration_minutes:60, location:"ufficio"}], todos:[]}`.
6. `_write_extracted` → `upsert_event` → cerca event esistente, non trova → crea su Calendar primary, scrive `WriteLog`.
7. Listener marca `processed=True`.

### Esempio 2: registrazione Plaud

1. Timer ogni 10 min lancia `big-sync-plaud.service`.
2. `plaud_sync` → lista cloud Plaud (3 nuove registrazioni) → download S3 → 3 record `PlaudRecording`.
3. `plaud_process_pending` → per ognuna: upload Gemini File API, wait ACTIVE, transcribe → salva `transcription`, `processed=True`. Logga token usage.
4. `plaud_summarize_pending` → per ognuna: `summarize_transcription(text)` → `(title, summary)`, salva. Poi chiama `process_realtime_message` → entità estratte → contatti/eventi/todo creati su Workspace.

### Esempio 3: email importante non taggata

1. Timer ogni 30 min lancia `gmail_sync`.
2. `gmail_sync` fa `users.history.list(startHistoryId=...)` → 5 nuove email.
3. Per ognuna `parse_message` + salva `GmailMessage` con `analyzed=False`.
4. Manualmente l'utente clicca "Analizza" sulla dashboard `/email/` → `run_command("gmail_analyze")` via SSE.
5. `gmail_analyze` itera, per ogni mail chiama `tag_email(...)` → tag suggeriti, crea/usa `EmailTag`, applica label Gmail via `apply_labels_to_message`. Marca `analyzed=True`.

---

## 14. Convenzioni & invarianti

- **Single-session login**: solo un device alla volta loggato. Login successivo invalida quello precedente.
- **Tutti i timestamp in UTC** in DB. Conversione a Europe/Rome solo in display.
- **Dedupe doppia**: prima cache locale (tabella `Contact` per i contatti), poi check via API Google prima di scrivere. Mai scrivere senza prima cercare.
- **WriteLog è append-only**: ogni scrittura su Workspace produce una riga. Mai cancellare.
- **Audio non trascritti = `transcription=""`**, audio trascritti vuoti (silenzio) = `transcription=""` + `error="empty transcription"`. Distinguibili guardando `processed` (True dopo tentativo).
- **Token AI**: tutte le chiamate Gemini usano `gemini-2.5-flash` come default (econ + qualità sufficiente). TTS usa `gemini-2.5-flash-preview-tts`. Plaud transcribe traccia token usage per misurare costo.
- **Media files** (`media/`) sono **gitignored**. Backup separato.
- **`tmp/`** è gitignored, usato per script di test e esperimenti.
- **`.claude/`** è gitignored.

---

## 15. File rilevanti fuori dal codice

- `IDEAS.md` — backlog idee e proposte (non versionato strict).
- `MISSING.md` — checklist features mancanti.
- `telegram_chats.txt` — dump nomi chat per debug.
- `requirements.txt` — pin versioni Python.
- `.gitignore` — esclude `media/`, `*.session`, `.env*`, `tmp/`, `.claude/`, `CREDENTIALS.md`.
- `CREDENTIALS.md` — **non versionato**, contiene le credenziali in chiaro per recovery.
