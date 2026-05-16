# big-sync

Aggregatore personale di messaggi, audio e notizie con automazione AI. Le fonti vengono ingerite localmente, analizzate da Gemini e materializzate come contatti, eventi calendario e todo su Google Workspace dell'utente. Una dashboard web mostra lo stato di ogni flusso, permette di lanciare ogni operazione manualmente, gestire i contatti duplicati e modificare eventi.

Questo documento descrive **cosa fa l'applicazione**, sezione per sezione, modello per modello. Per le credenziali vedere `CREDENTIALS.md` (non versionato).

---

## 1. Panoramica

### Cosa fa, in concreto

1. **Ingerisce** messaggi e contenuti da fonti esterne (Telegram, WhatsApp, Gmail, RSS, registratore audio Plaud).
2. **Salva tutto in DB** (PostgreSQL in produzione, SQLite in locale) — i messaggi non vengono mai mostrati direttamente all'utente, sono il dato grezzo su cui ragiona l'AI.
3. **Trascrive gli audio** (Telegram voice, WhatsApp audio, Plaud) usando Gemini File API.
4. **Analizza ogni messaggio** con un workflow Gemini dedicato che estrae JSON strutturato `{contacts, events, todos}`.
5. **Scrive su Google Workspace** con dedupe difensiva (cache locale + AI smart-dedup + routing per calendario): contatti in Google Contacts (con nicknames e merged_into), eventi/todo distribuiti su 5 calendari (Personal, Work, Chiara, Famiglia, University), note lunghe in Google Drive.
6. **Tiene una cache locale di TUTTI gli eventi** dei 5 calendari Google (`CachedEvent`) sincronizzata ogni 15 min — questa è la base per dedup, routing e per la pagina di editing manuale.
7. **Logga ogni call AI** in tabella `Usage` (tokens, costo, latency, source) e mostra dashboard a `/usage/`.
8. **Mostra una dashboard** con KPI per fonte, stato delle pipeline, log delle ultime attività, e pulsanti per ri-lanciare manualmente i comandi più rilevanti.

### Fonti attive vs. placeholder

| Fonte | Stato | Pipeline |
|-------|-------|----------|
| **Telegram** | Live | Listener daemon (Telethon) + analyzer batch |
| **WhatsApp** | Live | Listener daemon (neonize) + analyzer batch |
| **Plaud** (registratore audio) | Live | Cron sync da cloud Plaud + trascrizione + riassunto |
| **Email / Gmail** | Live | Cron import + analyzer (tagging) |
| **RSS** | Live | Cron fetch + classifier + summarizer + briefing audio per-topic |
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
- **AI**: Google Gemini (`gemini-2.5-flash` per testo, trascrizione e dedup, `gemini-2.5-flash-preview-tts` per il briefing audio).
- **Output API**: Google People API (Contacts), Google Calendar API, Google Drive API.
- **Web server prod**: gunicorn dietro nginx, autenticazione single-session a password.
- **Background jobs**: tutti via systemd (sei timer + due daemon long-running, dettaglio in §12). Nessun cron tradizionale, niente Celery.
- **Frontend dashboard**: HTML statico + isole di JavaScript vanilla. Polling JSON (RSS audio async, contacts merge, items edit/delete) e SSE per comandi shell esposti.

---

## 3. Struttura del progetto

```
big-sync/
├── config/                 # settings.py, urls.py, wsgi.py
├── common/                 # Auth, billing, dashboard, modelli condivisi (Contact, CachedEvent, WriteLog), calendars.py
├── usage/                  # Tracking provider-agnostic delle chiamate AI (Usage model + dashboard /usage/)
├── workflows/              # Pipeline AI (gemini.py, prompts.py, tts.py, workflow_*.py, dedup.py, routing.py, pricing.py, usage_logger.py)
├── outputs/                # Scrittura su Google Workspace (contacts, calendar, todos, drive)
├── sources/                # Una app Django per fonte
│   ├── telegram/           ├── whatsapp/   ├── plaud/
│   ├── email_source/       ├── rss/        ├── teams/
│   ├── clickup/            ├── sms/        ├── github/
│   ├── drive/              ├── home_assistant/
├── scripts/                # Setup oneshot (oauth, fix migrazioni)
├── media/                  # File scaricati (audio Plaud, voice Telegram, allegati WA, briefing audio RSS) — gitignored
└── manage.py
```

Ogni app sotto `sources/` è autosufficiente: ha i propri model, le proprie migrations, i propri management commands. Le sei app placeholder (Teams, ClickUp, SMS, GitHub, Drive, Home Assistant) contengono solo lo scheletro Django.

---

## 4. App `common` — fondazioni condivise

### `common/models.py`

| Modello | Cosa contiene |
|---------|---------------|
| `Contact` | Cache locale dei contatti Google. Campi: `resource_name` (id People API), `name`, `phones` (JSON list digits-only), `emails` (JSON list lowercase), `company`, `role`, `notes` (testo libero), `notes_url` (link Drive quando le note crescono), `aliases` (JSON list di nicknames/diminutivi lowercase), `merged_into` (FK self nullable — punta al canonical se questo è un duplicato). Metodo `.resolve()` segue la catena merged_into fino al canonical. |
| `CachedEvent` | Cache locale di TUTTI gli eventi su tutti i calendari Google. Chiave naturale `(google_id, calendar_id)`. Campi: `title`, `start_at`, `end_at`, `all_day`, `location`, `description`, `attendees` (JSON), `meet_link`, `organizer_email`, `is_todo` (true se titolo inizia con `[todo]`), `raw` (payload Google completo), `last_seen_at`, `synced_at`, `deleted_at` (soft-delete quando l'evento sparisce da Google). Popolato da `sync_calendar` ogni 15 min. È la base per dedup eventi, routing per calendario e per la pagina `/items/`. |
| `WriteLog` | Log immutable di ogni scrittura effettuata su Google Workspace. Campi: `type` (`contact`/`event`/`task`), `title`, `detail`, `created_at`. Alimenta la sezione "Attività recente" della home. |
| `ContactsSyncLog` | Timestamp + count dell'ultima full sync rubrica → DB locale. |
| `ActiveSession` | Singolo record che memorizza la session key dell'unico utente loggato in dashboard. Serve a **invalidare la sessione precedente** quando qualcuno fa login da un altro device. |

### `common/calendars.py`

Identifier hardcoded dei 5 calendari Google + tabella di routing. Mappa che dice quale calendario riceve cosa:

```python
ROUTE_TO_CALENDAR = {
    "work":     WORK_CALENDAR_ID,
    "chiara":   CHIARA_CALENDAR_ID,
    "personal": PRIMARY_CALENDAR_ID,  # default
}
```

`CALENDAR_LABEL` fa il reverse lookup per la UI. Famiglia e University sono noti ma non destinazioni di routing (per ora).

### `common/google_auth.py`

`get_credentials()` costruisce credenziali OAuth riutilizzabili. L'app supporta tre refresh token separati per Workspace (`GOOGLE_REFRESH_TOKEN`, `GOOGLE_REFRESH_TOKEN_CONTACTS`, `GOOGLE_REFRESH_TOKEN_CALENDAR`).

### `common/google_billing.py`

Wrapper sull'API Cloud Billing Budgets. `billing_summary()` ritorna spesa attuale + budget configurato.

### `common/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `sync_contacts` | Full reload rubrica Google → tabella `Contact`. Legge anche `nicknames` da Google e li unisce a `Contact.aliases`. Preserva i merge locali (`merged_into`). |
| `sync_calendar` | Pull eventi da TUTTI e 5 i calendari Google nella finestra `--past-days/--future-days` (default 60/180). Upsert in `CachedEvent`, soft-delete entries che spariscono. Normalizza l'id del primary a `"primary"` per coerenza con outputs. |
| `find_similar_contacts` | **Read-only**: lista gruppi di contatti potenzialmente duplicati. Esatto su nome normalizzato, e con `--fuzzy --max-dist N` anche Levenshtein ≤ N. Output mostra per ogni gruppo i campi utili per decidere (telefoni, email, company, alias, drive=YES). |
| `push_aliases_to_google` | Backfill: spinge `Contact.aliases` esistenti su Google come `nicknames`. Idempotente. Default dry-run, `--apply` per scrivere. |
| `dedup_contacts` | Bucket per primo nome, AI Gemini propone merge groups, scrive `merged_into` + accumula aliases/phones/emails sul canonical. Default dry-run, `--apply` per applicare. |
| `dedup_calendar` | Per ogni giorno con ≥2 eventi su un calendario, AI raggruppa duplicati (cross-lingua, dettagli diversi), tiene il più ricco e cancella gli altri via Google API + soft-delete cache. `--past-days/--future-days/--calendar/--apply`. |
| `clean_noisy_todos` | Rivede todo storici con il filtro AI `is_useful_todo` (`workflows/dedup.py`), elimina quelli classificati come rumore. `--past-days/--future-days/--calendar/--apply/--limit`. |
| `route_calendar` | Riclassifica eventi su primary e li sposta su Work / Chiara / Personal via AI batch classifier (`workflows/routing.py`). Per default analizza l'INTERA cache (passato e futuro). `--apply` per eseguire le `events.move()` su Google. |
| `migrate_notes_to_drive` | Comando one-shot legacy: sposta le note inline grandi su Google Drive. |

### `common/views.py` — la dashboard web

Tutte le view sono protette da `_is_authenticated()` (`request.session[_SESSION_KEY] is True` E `session_key == ActiveSession.get_current_key()`).

| URL | View | Cosa mostra |
|-----|------|-------------|
| `/` | `home` | Skeleton minimal — i KPI caricati async via `/api/home-stats/`. |
| `/api/home-stats/` | `home_stats_json` | JSON: stato Gemini, billing, conteggi contatti/eventi/task per fonte, 10 ultime attività. |
| `/telegram/`, `/whatsapp/`, `/plaud/`, `/email/`, `/rss/` | Dashboard per fonte. |
| `/email/<gmail_id>/` | `email_detail` | Vista singola email. |
| `/rss/` | `rss_dashboard` | Default tab **"Riassunto AI"** (oggi era "Notizie"). Tab "Notizie" disponibile. Mostra audio briefing per topic con player inline, pulsante "Genera mancanti", auto-advance al successivo quando un audio finisce. |
| `/rss/audio/<YYYY-MM-DD>/<topic_slug>/` | `rss_audio` | Streaming WAV del briefing di una sezione. |
| `/api/rss_audio_start/<YYYY-MM-DD>/` | `rss_audio_start` | POST: avvia la generazione async (Popen detached). Ritorna `job_id`. Idempotente: se un job è già in corso ne ritorna l'id. |
| `/api/rss_audio_status/<YYYY-MM-DD>/` | `rss_audio_status` | GET: stato del job + inventario sezioni. La pagina lo polla ogni 3s. |
| `/plaud/upload/` | `plaud_upload` | POST multipart per upload manuale di file audio. |
| `/items/` | `items_dashboard` | Tabelle di **eventi e todo cacheati** (da `CachedEvent`). Editing inline del titolo + bottone "Elimina" su ogni riga. Filtro temporale `?past=N&future=M`. |
| `/items/<google_id>/` | `item_action` | POST JSON `{action: "delete"\|"update", calendar_id, title?, location?, description?}`. Wrappa `outputs.calendar.update_event/delete_event`. |
| `/contacts/` | `contacts_dashboard` | **Pagina admin merge contatti**: lista gruppi nome esatto + gruppi simili (Levenshtein ≤ `?max_dist=N`). Per ogni gruppo: radio canonical + checkbox merge + bottone "Unisci" + checkbox "elimina duplicati da Google". |
| `/contacts/merge/` | `contacts_merge_action` | POST JSON `{canonical_id, merge_ids: [...], delete_google: bool}`. Chiama `outputs.contacts.merge_contacts`. |
| `/usage/` | `usage_dashboard` | Recap 30 giorni delle chiamate AI: costo totale, breakdown per source/model/operation/giorno, ultime 50 chiamate, errori recenti. |
| `/teams/`, `/clickup/`, `/sms/`, `/github/`, `/gdrive/`, `/homeassistant/` | Placeholder "coming soon". |
| `/login/`, `/logout/` | Login a password (`DASHBOARD_PASSWORD`). Single-session enforcement. |
| `/run/<action>/` | `run_command` | Esegue uno dei management commands ammessi (whitelist) e streamma stdout via SSE. |
| `/admin/` | Django admin. |

### Template `common/templates/common/`

- `_sidebar.html` — barra laterale, icone SVG per: Overview, Telegram, WhatsApp, Plaud, Email, Teams, ClickUp, SMS, GitHub, Drive, HomeAssistant, RSS, **Eventi & Todo**, **Contatti**, **Usage**, Logout.
- `home.html` — overview con KPI async-load.
- Dashboard per fonte: `telegram.html`, `whatsapp.html`, `plaud.html`, `email.html`, `email_detail.html`, `rss.html`, `rss_article.html`.
- Pagine admin: `items.html` (eventi/todo), `contacts.html` (merge interattivo).
- Utility: `login.html`, `placeholder.html`, `dashboard.html`.

Tutti i template condividono CSS dark inline (palette `#0f1117` bg, `#6366f1` accent, sidebar icon inattive `#64748b`).

---

## 5. App `sources/telegram`

### `sources/telegram/models.py`

`TelegramMessage` — un record per messaggio. Chiave naturale `(chat_id, message_id)`. Campi: `chat_id` (BigInteger, index), `chat_name`, `sender_id`, `sender_name`, `text`, `media_type`, `media_downloaded`, `media_path`, `transcription`, `date`, `raw` (dump JSON Telethon), `processed`, `created_at`.

### `sources/telegram/media.py`

Helpers: ignore chat/entity (`TELEGRAM_IGNORE_CHATS`), detect media type, slugify nome chat, serializzazione raw event.

### `sources/telegram/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `telegram_auth` | One-shot interattivo: login con codice OTP, salva file di sessione. |
| `telegram_listener` | **Daemon long-running**. Telethon → eventi `NewMessage` → salva DB → trascrive audio (con `source="telegram"` per usage tracking) → `process_realtime_message(..., source="telegram")`. |
| `telegram_import_history` | Importa storico chat per chat, modalità backfill/`--gap-check`. Rispetta `TELEGRAM_IGNORE_CHATS`. |
| `telegram_analyze_history` | Analizza messaggi non processati in batch giorno-per-chat. |
| `telegram_backfill_todos` | Migrazione storica Google Tasks → eventi calendario `[todo]` con slot finder. |

---

## 6. App `sources/whatsapp`

Mirror di `telegram` — stesso flusso listener+analyzer.

### `sources/whatsapp/models.py`

`WhatsAppMessage` — chiave `(chat_jid, message_id)`. Aggiunge `is_from_me`, `is_group`.

### `sources/whatsapp/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `whatsapp_pair` | Pairing iniziale (legacy, il listener stesso gestisce il pair). |
| `whatsapp_listener` | **Daemon long-running**. neonize → eventi → analisi inline con `source="whatsapp"`. |
| `whatsapp_analyze_history` | Batch giorno-per-chat. |

**Importante**: WhatsApp multi-device permette il dump dello storico **una sola volta**, al pairing. Per recuperare storico dopo perdita serve rifare il pairing.

---

## 7. App `sources/plaud`

Integrazione registratore vocale **Plaud Note** senza abbonamento. API reverse-engineered.

### `sources/plaud/models.py`

`PlaudRecording`: `plaud_id` (unique), `file`, `original_name`, `size_bytes`, `duration_ms`, `serial_number`, `transcription`, `title`, `summary`, `processed`, `summarized`, `error`, `recorded_at`, `created_at`.

### `sources/plaud/client.py`

Client HTTP minimale. Auth Bearer JWT (`PLAUD_TOKEN`), region detection auto-correct via `status:-302`. Endpoint principali: list recordings, presigned audio URL.

### `sources/plaud/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `plaud_sync` | Cloud → dedupe per `plaud_id` → download stream → record `PlaudRecording`. |
| `plaud_process_pending` | Trascrive con `transcribe_audio(..., source="plaud", ref_id=rec.pk)`. Logga token usage. |
| `plaud_summarize_pending` | `summarize_transcription(..., source="plaud")` → title+summary → poi `process_realtime_message(..., source="plaud")` estrae entità. |

I tre concatenati in `big-sync-plaud.service` (timer ogni 10 min).

---

## 8. App `sources/email_source` (Gmail)

### Modelli

| Modello | Cosa contiene |
|---------|---------------|
| `EmailTag` | `name`, `color`, `gmail_label_id`. Tag predefiniti seedati a runtime. |
| `GmailMessage` | `gmail_id`, `thread_id`, `subject`, `sender`, `sender_email`, `snippet`, `body_text`, `date`, `gmail_labels`, `tags` (M2M), `analyzed`. |
| `GmailSyncState` | Singleton: ultimo `history_id` per incremental. |

### Commands

| Comando | Cosa fa |
|---------|---------|
| `gmail_import --full` | Prima esecuzione full. |
| `gmail_sync` | Incremental smart, lanciato da timer 30 min. |
| `gmail_analyze` | Per ogni mail `analyzed=False`: `tag_email(..., source="email")` (Gemini) → label Gmail. |

---

## 9. App `sources/rss`

Aggregatore RSS con **classificazione per topic** e **briefing audio giornaliero per-topic, asincrono**.

### `sources/rss/models.py`

| Modello | Cosa contiene |
|---------|---------------|
| `RssFeed` | `name`, `url` (unique), `active`, `last_fetched`. Lista seedata da `_seed_feeds()` in `rss_fetch.py`. |
| `RssArticle` | `feed` (FK), `guid` (unique), `title`, `url`, `summary`, `content`, `published_at`, `read`, `analyzed`. |
| `RssTopic` | `slug` (unique), `name`, `order`. 10 topic predefiniti (politica IT, politica intl, economia, tech, scienza, sport, cultura, cronaca, ambiente, altro). |
| `RssDailySummary` | `(topic, date)` unique, `text` (markdown), `article_count`, `updated_at`. Una riga per topic per giorno. |
| `RssDailyAudio` | `(topic, date)` unique, `file` (FileField → `media/rss_audio/{date}/{topic_slug}.wav`), `summary_updated_at` (snapshot di RssDailySummary.updated_at al momento della generazione → permette di marcare audio "stale" quando la sezione viene rianalizzata), `generated_at`. |
| `RssAudioJob` | Tracking di una run async di `rss_audio_generate`. Campi: `date`, `status` (`running`/`done`/`error`), `total_sections`, `completed_sections`, `current_topic_slug`, `pid`, `started_at`, `updated_at`, `finished_at`, `error`. |

### `sources/rss/management/commands/`

| Comando | Cosa fa |
|---------|---------|
| `rss_fetch` | Per ogni feed attivo: parse con `feedparser`, dedupe per `guid`. URL hardcoded in `DEFAULT_FEEDS` (incluse correzioni post-deprecazioni: Corriere usa `xml2.corriereobjects.it`, Il Post usa `feeds.feedburner.com/ilpost`). |
| `rss_analyze` | Per ogni `RssArticle.analyzed=False`: `classify_article(..., source="rss")` → topic, poi `merge_into_summary(..., source="rss")` → estende `RssDailySummary`. Niente retention/cleanup automatici (gli articoli vecchi restano). |
| `rss_update` | Wrapper `rss_fetch` + `rss_analyze`. Timer 5 min. |
| `rss_audio_generate` | Per ogni `RssDailySummary` con `article_count>0`: se manca o è stale (`summary_updated_at < summary.updated_at`), genera WAV con `generate_section_wav(topic_name, text, source="rss")`. Aggiorna `RssDailyAudio`. Con `--job-id` aggiorna il tracking di `RssAudioJob` per il polling UI. `--force` rigenera tutto. |

### Generazione audio async (UX)

1. Utente clicca "Genera mancanti" su `/rss/`.
2. JS chiama `POST /api/rss_audio_start/<date>/`.
3. View crea `RssAudioJob`, `Popen(... start_new_session=True)` la command → subprocess detached.
4. JS inizia polling `GET /api/rss_audio_status/<date>/` ogni 3s.
5. Il command aggiorna `completed_sections`/`current_topic_slug` a ogni sezione completata.
6. La pagina mostra il contatore, hot-swappa i player audio appena pronti, riabilita il pulsante quando `status=done`.
7. Se l'utente cambia pagina e torna, JS rileva `running=true` e riprende il polling automaticamente.
8. Su ogni audio: listener `ended` fa partire automaticamente il successivo (e apre il topic card relativo).

---

## 10. App `usage` — telemetria AI

### `usage/models.py`

`Usage` — provider-agnostic. Campi: `provider` ("gemini" oggi, altro domani), `model`, `operation` (transcribe / summarize / extract_batch / extract_realtime / extract_single / classify / merge_summary / tag_email / tts / tts_section / ask / ask_text / is_same_event / is_useful_todo / resolve_contact_alias / classify_batch), `source` (telegram / whatsapp / plaud / rss / email / dedup / routing / dedup_calendar / dedup_contacts / manual / unknown), `prompt_tokens`, `output_tokens`, `total_tokens`, `cost_usd` (Decimal), `duration_ms`, `ref_type`, `ref_id`, `error`, `created_at`. Indici su `(created_at, source)` e `(created_at, provider, model)`.

### Dashboard `/usage/`

Recap ultimi 30 giorni: KPI totali (costo USD, chiamate, token in/out/totali), breakdown per source / model / operation / giorno, tabella ultime 50 chiamate, errori recenti. Pills colorate per source.

---

## 11. Workflow AI (`workflows/`)

### `workflows/gemini.py` — primitive

- `_get_client()` — singleton `genai.Client` con HTTP timeout 180s.
- `_extract_usage(response)` — helper per pescare i token counts da `usage_metadata`.
- `transcribe_audio(file_path, model="gemini-2.5-flash", retries=4, return_usage=False, source="unknown", ref_id="")` — upload File API → poll `_wait_for_active` → `generate_content` con prompt italiano. Retry 5xx. Cleanup. Logga in `Usage`.
- `summarize_transcription(text, model="gemini-2.5-flash", source="plaud", ref_id="")` → `(title, summary)`. Formato delimitato `TITLE: ...\n---SUMMARY---\n<markdown>` (non JSON, perché il summary ha markdown).
- `ask_text(prompt, ..., source, operation, ref_id)` — generate_content text-only. Logga in `Usage`.
- `ask(prompt, ..., source, operation, ref_id)` — come sopra ma extracts JSON.
- `extract_json(text)` — tollera fence ```` ```json ````, estrae primo `{` / ultimo `}`.

Tutte le wrapper accettano `source` + `operation` + `ref_id` per il tracking.

### `workflows/prompts.py` — prompt builder

- `batch_prompt(chat_name, date, messages)`, `single_prompt(...)`, `realtime_prompt(...)` — enforcement schema `{contacts, events, todos}`.

### `workflows/workflow_telegram.py`

Pipeline analisi messaggi (usato da Telegram, WhatsApp e Plaud — nome storico). Funzioni `process_batch/process_message/process_realtime_message` accettano `source` per il tracking e propagano a `ask(...)`. `_write_extracted` itera contacts/events/todos e chiama upsert. Cattura eccezioni per item.

### `workflows/workflow_email.py`

`tag_email(sender, subject, body)` — Gemini → lista tag ammessi. Lista hardcoded nel prompt.

### `workflows/workflow_rss.py`

- `classify_article(title, text)` → topic name.
- `merge_into_summary(topic_name, current_summary, title, source, text)` → nuovo testo del sommario aggiornato.

### `workflows/tts.py`

- `_text_to_pcm(text, source, operation, ref_id)` — chiamata `gemini-2.5-flash-preview-tts`, ritorna PCM raw. Logga in `Usage` stimando audio tokens dai byte PCM.
- `text_to_wav(text, ...)` — wrapper che restituisce WAV bytes.
- `generate_section_wav(topic_name, text, source="rss", ref_id="")` — wrapper per RSS (prefisso "Sezione {topic_name}.").

### `workflows/dedup.py` — AI deduplication helpers

Tre judgment calls conservative (in caso di dubbio → "no/unsure"):

- `is_same_event(new_event, candidates) -> str|None` — riconosce duplicati eventi cross-lingua (es. "Compleanno di Gray" == "Gray's Birthday"), differenti livelli di dettaglio, orari leggermente diversi. Restituisce id del candidato matched o None.
- `is_useful_todo(title, context_chat, context_text) -> (bool, reason)` — filtro qualità: scarta istruzioni tecniche di sistema, azioni triviali quotidiane, descrizioni di stato (non-azioni), todo vaghi senza azione concreta. Default "keep" su errore.
- `resolve_contact_alias(name, phone, email, candidates) -> dict` — decide se un nome è alias di un contatto esistente (diminutivi, soprannomi, nome senza cognome). Restituisce `{match_id, alias_to_add, confidence, reason}`. **Nota**: non più usato da `outputs/contacts.py` (sostituito da match near-exact Levenshtein); resta disponibile per future strategie.

### `workflows/routing.py` — calendar router AI

- `classify_events_batch(events) -> {id: {route, confidence, reason}}` — batch API: classifica fino a 15 eventi in una call, decide "work" / "chiara" / "personal". Prompt include esempi delle entità lavorative (Erregame, Inspireng, Grimaldi, Polverini, Onecpas, Affri, Marvin, Francesco Circosta, Ace, Gray G, ecc.) e regola: usare "chiara" SOLO se "Chiara" è esplicitamente menzionata.
- `classify_event(event) -> str` — single-event wrapper. Default "personal" su confidence low.

Usato da:
- `outputs/calendar.py::upsert_event` e `outputs/todos.py::upsert_todo_event` (writes nuovi).
- `common/management/commands/route_calendar` (backfill storico).

### `workflows/pricing.py`

Tabella USD per 1M token, per `(provider, model)`. Funzioni `estimate_cost_usd` e `estimate_tts_audio_tokens` (~32 token/sec da PCM 24kHz mono 16-bit). Edit qui per aggiornare i prezzi.

### `workflows/usage_logger.py`

`log_usage(...)` — scrive una riga in `Usage`. **Mai solleva eccezioni** — la telemetria non deve mai rompere il workflow chiamante.

---

## 12. Outputs (scritture su Google Workspace)

### `outputs/contacts.py`

**Matching policy near-exact** (Levenshtein ≤ 2, entrambi i nomi ≥ 4 char; sotto si richiede match esatto):

1. Email esatta (lowercase) in `Contact.emails`.
2. Phone esatto (digits-only) in `Contact.phones`.
3. Nome esatto (`name__iexact`) o alias presente in `Contact.aliases`.
4. Scan near-exact su contatti che condividono la prima lettera (`name__istartswith`).

Tutti escludono `merged_into__isnull=False`. Match resolve via `.resolve()`.

`upsert_contact(data)`:
- Match found → `_enrich_contact`: aggiunge phones/emails/company/notes/aliases mancanti. Se il nome incoming differisce dal canonical e da tutti gli alias, viene registrato come **nuovo nickname su Google**.
- Match miss → `_create_contact`: crea su People API + cache locale.

`merge_contacts(canonical_id, merge_ids, delete_google=True)`:
1. Union phones/emails/aliases sul canonical. Carry company/role. Concat note Drive (separator `---`). Se i merged hanno una company diversa dal canonical, aggiunge "[Also at: X]" nelle note.
2. Update Drive file del canonical; cancella i Drive file dei merged.
3. Push aliases come `nicknames` su Google del canonical. Update organizations.
4. Se `delete_google=True`: cancella i contatti Google duplicati + cancella le righe locali. Altrimenti: marca solo `merged_into=canonical` (tombstone).

Usato dalla pagina `/contacts/` e via shell.

### `outputs/calendar.py`

`_find_existing(service, summary, date)` — prima cerca in `CachedEvent` (calendar=primary, ±3gg, titolo normalizzato). Cache miss → fallback Google API.

`_find_existing_ai(service, data)` — second-pass per fuzzy duplicates (lingue diverse, dettagli diversi): pulla candidati ±3gg da `CachedEvent`, chiama `is_same_event` (Gemini).

`upsert_event(data)`:
1. Skip se `confidence=="low"`.
2. `_find_existing` → enrich se trova.
3. `_find_existing_ai` → enrich se trova.
4. Altrimenti `_build_body` + `_route_calendar_for(data)` (chiama `classify_event` per decidere Work/Chiara/Personal) → insert su quel calendario.

`update_event(google_id, calendar_id, fields)` — PATCH evento + refresh `CachedEvent` (titolo, location, description, raw).

`delete_event(google_id, calendar_id)` — DELETE su Google + soft-delete `CachedEvent`.

### `outputs/todos.py`

I todo sono **eventi calendario con prefisso `[todo] `**. `upsert_todo_event(data, fallback_datetime)`:
1. Skip se `assigned_to` ≠ me.
2. **AI quality filter** (`is_useful_todo`) — scarta rumore prima di creare nulla.
3. `_parse_start(date, time)` con fallback al datetime del messaggio.
4. `_find_existing` (consulta `CachedEvent` prima, fallback Google).
5. Insert con `_route_calendar_for_todo` (router AI).

Helper:
- `find_free_slot(...)` — slot finder per backfill (ignora all-day events).
- `create_todo_event_at(...)` — usato da `telegram_backfill_todos`.

### `outputs/tasks.py` (legacy)

Google Tasks API. Deprecato dopo lo switch a `upsert_todo_event`. Mantenuto per riferimento.

### `outputs/drive.py`

`append_contact_note(contact_name, full_notes, notes_url)` — crea/aggiorna Doc in cartella `Contatti`, ritorna URL. Usata sia da `upsert_contact` che da `merge_contacts`.

---

## 13. Background services & cron

Tutto su systemd (server). Niente cron tradizionale.

### Daemon long-running

| Service | Comando |
|---------|---------|
| `big-sync.service` | gunicorn — dashboard + admin. |
| `big-sync-telegram.service` | `manage.py telegram_listener`. Restart on failure. |
| `big-sync-whatsapp.service` | `manage.py whatsapp_listener`. Restart on failure. |

### Timer

| Timer | Frequenza | Service |
|-------|-----------|---------|
| `big-sync-rss-update.timer` | 5 min | `rss_update` (fetch + analyze) |
| `big-sync-gmail-sync.timer` | 30 min | `gmail_sync` |
| `big-sync-plaud.timer` | 10 min | `plaud_sync` → `plaud_process_pending` → `plaud_summarize_pending` |
| `big-sync-calendar-sync.timer` | 15 min | `sync_calendar` — popola `CachedEvent` da tutti i calendari Google |

Note:
- `gmail_analyze` non ha timer — manuale dalla UI.
- I listener Telegram/WhatsApp fanno analisi inline (chiamano `process_realtime_message` per ogni messaggio nuovo). I `*_analyze_history` sono per backfill manuale.
- `rss_audio_generate` non ha timer: viene lanciato on-demand dalla UI (async detached).

---

## 14. Flussi end-to-end (esempi)

### Esempio 1: messaggio Telegram con un appuntamento di lavoro

1. Listener riceve `NewMessage` da "Marco Polverini", "domani 15 call su gara nuova".
2. Salva `TelegramMessage` + scarica eventuali media.
3. `process_realtime_message("Marco Polverini", new_msg, context, source="telegram")` → `realtime_prompt` → Gemini → JSON `{events: [{summary: "Call Polverini gara", ...}]}`.
4. `_write_extracted` → `upsert_event(...)`:
   - `_find_existing` (cache) → miss.
   - `_find_existing_ai` (Gemini su CachedEvent ±3gg) → miss.
   - `_route_calendar_for(data)` → `classify_event` → "work" → calendario Work.
   - `insert` su Work calendar, `WriteLog`.
5. Listener marca `processed=True`, scrive riga `Usage`.

### Esempio 2: registrazione Plaud → contatto con alias

1. Timer plaud (10 min) → `plaud_sync` → nuova registrazione.
2. `plaud_process_pending` → `transcribe_audio(..., source="plaud")` → Usage row.
3. `plaud_summarize_pending` → `summarize_transcription(...)` → `process_realtime_message(..., source="plaud")` → estrae contatto "Ghira".
4. `upsert_contact({name: "Ghira"})`:
   - `_find_existing_local` — email/phone vuoti, `name__iexact` miss, `aliases__contains=["ghira"]` → match su Contact "Ghiraffa Rossi" (alias già presente da merge precedente).
   - `_enrich_contact` — nulla da aggiungere.

### Esempio 3: deduplica contatti via pagina admin

1. Utente apre `/contacts/?max_dist=1`.
2. Vede gruppi: `Connie/Conny`, `Mubashir/mumbashir`, ecc.
3. Per ogni gruppo: seleziona canonical (radio), checkbox quali fondere, cliccca "Unisci".
4. `POST /contacts/merge/` → `merge_contacts(...)` → cache locale + Drive notes + Google nicknames + (opzionale) delete duplicati Google.
5. UI marca le righe fuse come `gone` (strikethrough).

### Esempio 4: ascolto briefing audio RSS

1. Utente apre `/rss/` (default tab "Riassunto AI").
2. Clicca "Genera mancanti".
3. `POST /api/rss_audio_start/<oggi>/` → forka `rss_audio_generate --job-id N` detached.
4. JS polla `GET /api/rss_audio_status/<oggi>/` ogni 3s, mostra `Generazione 3/10 · tecnologia`.
5. Quando una sezione completa, l'audio appare inline.
6. Quando finisce un audio, parte automaticamente il successivo (listener `ended`).

---

## 15. Convenzioni & invarianti

- **Single-session login**: solo un device alla volta loggato.
- **Tutti i timestamp in UTC** in DB. Conversione a Europe/Rome solo in display.
- **Dedup multi-layer**: cache locale (`Contact`, `CachedEvent`) prima, poi Google API. Per i nomi, near-exact Levenshtein ≤ 2 (non più AI fuzzy). Per gli eventi, anche un second-pass AI (`is_same_event`).
- **WriteLog è append-only**: ogni scrittura su Workspace produce una riga.
- **Aliases bidirezionali**: `Contact.aliases` ↔ Google `nicknames`. `sync_contacts` li unisce.
- **`merged_into` = tombstone**: un contatto fuso non viene mai più usato per match; redirige sempre al canonical via `.resolve()`.
- **Routing calendario**: writes nuovi passano per il classifier AI. Default "personal" su confidence low/error.
- **Audio RSS stale-detection**: `RssDailyAudio.summary_updated_at` snapshot di `RssDailySummary.updated_at` → quando il sommario cambia, l'audio diventa "obsoleto" e va rigenerato.
- **Usage tracking non blocca mai**: `log_usage` swallow tutte le exception.
- **Niente retention RSS**: articoli e summary giornalieri non vengono mai cancellati.
- **Media files** (`media/`) sono **gitignored**. Backup separato.
- **`tmp/`** e **`.claude/`** sono gitignored.

---

## 16. File rilevanti fuori dal codice

- `IDEAS.md` — backlog idee.
- `MISSING.md` — checklist features mancanti.
- `telegram_chats.txt` — dump nomi chat per debug.
- `requirements.txt` — pin versioni Python.
- `.gitignore` — esclude `media/`, `*.session`, `.env*`, `tmp/`, `.claude/`, `CREDENTIALS.md`.
- `CREDENTIALS.md` — **non versionato**, credenziali in chiaro per recovery (vedi anche `common/calendars.py` per gli ID Google Calendar).
