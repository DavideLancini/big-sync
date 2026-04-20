"""AI tagging for Gmail messages."""
import logging

from workflows.gemini import ask_text

logger = logging.getLogger(__name__)

_TAG_DESCRIPTIONS = """- Lavoro: email professionale, colleghi, clienti, progetti lavorativi
- Personale: amici, famiglia, comunicazioni private
- Finanze: fatture, pagamenti, estratti conto, banca, abbonamenti, rimborsi
- Acquisti: ordini online, spedizioni, conferme acquisto, e-commerce, ricevute
- Newsletter: newsletter periodiche, digest, aggiornamenti da siti/brand
- Notifiche: notifiche automatiche da app/servizi, alert, OTP, codici di verifica
- Viaggi: voli, hotel, trasporti, prenotazioni, itinerari
- Salute: medici, farmaci, appuntamenti sanitari, referti, assicurazione salute
- Urgente: richiede azione immediata o ha scadenza imminente
- Unsubscribe: l'email contiene un link o testo "unsubscribe", "cancella iscrizione", "opt-out" o simili
- Spam: email indesiderata, phishing, truffa, pubblicità non richiesta"""

_TAG_NAMES = [
    "Lavoro", "Personale", "Finanze", "Acquisti", "Newsletter",
    "Notifiche", "Viaggi", "Salute", "Urgente", "Unsubscribe", "Spam",
]


def tag_email(sender: str, subject: str, body: str) -> list[str]:
    """
    Return a list of 1–3 tag names for the given email.
    Always returns at least one tag from _TAG_NAMES.
    """
    prompt = (
        f"Analizza questa email e assegna da 1 a 3 tag dalla lista seguente.\n"
        f"Rispondi SOLO con i nomi dei tag separati da virgola, esattamente come scritti.\n\n"
        f"Tag disponibili:\n{_TAG_DESCRIPTIONS}\n\n"
        f"Da: {sender}\n"
        f"Oggetto: {subject}\n"
        f"Testo: {body[:800]}\n\n"
        f"Tag:"
    )
    raw = ask_text(prompt).strip()

    tags = []
    for part in raw.split(","):
        name = part.strip().rstrip(".")
        if name in _TAG_NAMES:
            tags.append(name)

    if not tags:
        logger.warning("tag_email: no valid tags in %r for subject %r", raw, subject[:60])
        tags = ["Notifiche"]

    return tags[:3]
