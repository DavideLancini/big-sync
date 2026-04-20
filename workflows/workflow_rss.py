"""Gemini-powered RSS article classification and summary merging."""
import logging

from workflows.gemini import ask_text

logger = logging.getLogger(__name__)

TOPIC_NAMES = [
    "Politica italiana",
    "Politica internazionale",
    "Economia & finanza",
    "Tecnologia & AI",
    "Scienza & salute",
    "Sport",
    "Cultura & spettacolo",
    "Cronaca",
    "Ambiente & clima",
    "Altro",
]

_TOPICS_LIST = "\n".join(f"- {t}" for t in TOPIC_NAMES)


def classify_article(title: str, text: str) -> str:
    """Return the topic name that best matches the article. Always returns a valid topic."""
    prompt = (
        f"Classifica questa notizia in UNA delle categorie seguenti.\n"
        f"Rispondi SOLO con il nome esatto della categoria, senza altro testo.\n\n"
        f"Categorie:\n{_TOPICS_LIST}\n\n"
        f"Titolo: {title}\n"
        f"Testo: {text[:600]}\n\n"
        f"Categoria:"
    )
    result = ask_text(prompt).strip()
    for t in TOPIC_NAMES:
        if result == t:
            return t
    # fuzzy fallback: check if any topic name is contained in the response
    for t in TOPIC_NAMES:
        if t.lower() in result.lower():
            return t
    logger.warning("classify_article: unrecognized topic %r for %r — using Altro", result, title[:60])
    return "Altro"


def merge_into_summary(topic_name: str, current_summary: str, title: str, source: str, text: str) -> str:
    """Merge a new article into the running daily summary for a topic."""
    if current_summary.strip():
        context = f"Riassunto attuale della categoria '{topic_name}':\n{current_summary}\n\n"
    else:
        context = f"Non esiste ancora un riassunto per la categoria '{topic_name}'.\n\n"

    prompt = (
        f"{context}"
        f"Nuova notizia da integrare:\n"
        f"Fonte: {source}\n"
        f"Titolo: {title}\n"
        f"Testo: {text[:800]}\n\n"
        f"Istruzioni:\n"
        f"- Se la notizia è già presente nel riassunto (stessa storia da fonte diversa), "
        f"aggiorna con eventuali nuovi dettagli o angolazioni\n"
        f"- Se è una notizia nuova, aggiungila al riassunto\n"
        f"- Mantieni il riassunto conciso ma completo (max 400 parole)\n"
        f"- Scrivi in italiano, tono giornalistico neutro\n"
        f"- Usa paragrafi separati per argomenti/storie diversi\n"
        f"- Non usare markdown, titoletti o elenchi puntati\n"
        f"- Rispondi SOLO con il testo del riassunto aggiornato"
    )
    return ask_text(prompt)
