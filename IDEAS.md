# IDEAS — Espansioni possibili

---

## Fonti in entrata aggiuntive

> Le fonti già integrate nel progetto non compaiono qui.

### Messaggistica
| Fonte | Cosa offre |
|-------|-----------|
| **Slack** | Canali, DM, thread, menzioni, file condivisi |
| **Discord** | Server, canali, DM, thread |
| **LinkedIn Messages** | Messaggi diretti, richieste di connessione |
| **iMessage** | Via Apple Business Connect o bridge locale |
| **Signal** | Bridge via Signal-CLI (self-hosted) |
| **Viber** | Chatbot API |

### Email e calendario
| Fonte | Cosa offre |
|-------|-----------|
| **Outlook Calendar** | Via Microsoft Graph — eventi in arrivo, inviti |
| **Newsletter / digest** | Email di servizio con contenuto strutturato (Substack, ecc.) |

### Gestione progetti e ticket
| Fonte | Cosa offre |
|-------|-----------|
| **Notion** | Database, pagine, commenti |
| **Jira** | Issue, sprint, commenti, transizioni |
| **Linear** | Issue, progetti, aggiornamenti |
| **GitLab** | Issue, MR, commenti, review, release |
| **Asana** | Task, progetti, messaggi |
| **Trello** | Card, commenti, checklist |

### CRM e vendite
| Fonte | Cosa offre |
|-------|-----------|
| **HubSpot** | Deal, contatti, note, email tracciate |
| **Salesforce** | Opportunità, attività, log chiamate |
| **Pipedrive** | Deal, attività, note |

### Chiamate e meeting
| Fonte | Cosa offre |
|-------|-----------|
| **Zoom** | Trascrizioni meeting, chat in-call, registrazioni |
| **Google Meet** | Trascrizioni (via Workspace) |
| **Fireflies.ai / Otter.ai** | Trascrizioni automatiche di chiamate |
| **Loom** | Video messaggi con trascrizione |

### Documenti e note
| Fonte | Cosa offre |
|-------|-----------|
| **Obsidian** (locale) | Note personali via file watcher |
| **Roam Research** | Note e graph via API |
| **Apple Notes** | Bridge tramite export o shortcut |

### Dati web e social
| Fonte | Cosa offre |
|-------|-----------|
| **Twitter/X** | Menzioni, DM, thread |
| **Web scraping** | Pagine monitorate con variazioni di contenuto |

---

## Dati estraibili (oltre a contatti, eventi, todo)

### Da testo e conversazioni
- **Action item impliciti** — "ti mando il doc entro stasera" → todo con scadenza
- **Impegni presi da altri** — "Marco mi manda il preventivo domani" → reminder di follow-up
- **Decisioni prese** — log automatico di decisioni rilevanti
- **Sentiment e urgenza** — prioritizzazione todo in base al tono del messaggio
- **Lingua e preferenze** — profilo comunicativo del contatto
- **Domande senza risposta** — thread rimasti aperti da seguire

### Da email
- **Firme email** → arricchimento contatti (telefono, azienda, ruolo, LinkedIn)
- **Allegati** → estrazione dati da PDF, fatture, contratti
- **Thread di negoziazione** → sintesi e stato aggiornato
- **Scadenze contrattuali** → eventi calendario con alert anticipati

### Da meeting e chiamate
- **Sommario meeting** → nota strutturata con decisioni e next step
- **Partecipanti** → aggiornamento contatti e relazioni
- **Follow-up concordati** → todo assegnati per persona

### Da ClickUp / Jira / Linear
- **Dipendenze bloccate** → alert e reminder
- **Task scaduti senza aggiornamento** → nudge automatico
- **Pattern di carico lavoro** → segnalazioni di overload

---

## Output aggiuntivi (strumenti su cui agire)

### Comunicazione
| Strumento | Azioni possibili |
|-----------|-----------------|
| **Slack** | Inviare messaggi, creare reminder, aggiornare canali di stato |
| **Email (SMTP)** | Risposta automatica, digest giornaliero, follow-up |
| **Telegram** | Notifiche personali, alert, report |
| **WhatsApp** | Risposta automatica, notifiche |

### Produttività personale
| Strumento | Azioni possibili |
|-----------|-----------------|
| **Notion** | Creare pagine, aggiornare database, aggiungere log |
| **Obsidian** | Creare note via file system |
| **Todoist** | Creare e aggiornare task |
| **Things 3** | Creare task via URL scheme (Mac/iOS) |
| **Apple Reminders** | Creare reminder via Shortcuts |

### Gestione progetti
| Strumento | Azioni possibili |
|-----------|-----------------|
| **ClickUp** | Creare task, aggiornare stati, aggiungere commenti |
| **Jira** | Creare issue, aggiornare stato, aggiungere commento |
| **Linear** | Creare issue, assegnare, aggiornare priorità |
| **GitHub** | Aprire issue, aggiungere label, creare PR draft |

### CRM
| Strumento | Azioni possibili |
|-----------|-----------------|
| **HubSpot** | Creare/aggiornare contatti, log attività, creare deal |
| **Pipedrive** | Creare contatto, aggiungere nota, avanzare deal |
| **Salesforce** | Creare lead, log chiamata, aggiornare opportunità |

### Documenti e knowledge base
| Strumento | Azioni possibili |
|-----------|-----------------|
| **Google Docs** | Creare documento di sintesi, aggiungere commenti |
| **Notion** | Popolare database CRM interno, log decisioni |
| **Confluence** | Creare/aggiornare pagine di progetto |

### Automazione e notifiche
| Strumento | Azioni possibili |
|-----------|-----------------|
| **Zapier / Make** | Triggerare zap/scenario da big-sync via webhook |
| **Webhook custom** | Notificare sistemi interni |
| **Pushover / Ntfy** | Notifiche push su mobile self-hosted |
| **Home Assistant** | Trigger automazioni domestiche/ufficio |

### Analisi e reportistica
| Strumento | Azioni possibili |
|-----------|-----------------|
| **Google Sheets** | Log strutturato di tutti gli eventi processati |
| **Metabase / Grafana** | Dashboard su volumi, fonti, tipi di azione |
| **Airtable** | Database relazionale di contatti e interazioni |
