"""Calendar identifiers and routing destinations.

Hardcoded so that outputs/*.py and the routing command share a single source
of truth. If a calendar gets renamed or re-created, update the id here.
"""

PRIMARY_CALENDAR_ID = "primary"
CHIARA_CALENDAR_ID = (
    "b6087b4ff2d10f484e64721e40d73bc59598395107989daf8938b1a71a024e5e"
    "@group.calendar.google.com"
)
WORK_CALENDAR_ID = "3d215o54t44pjnlrj081l8jel0@group.calendar.google.com"
FAMIGLIA_CALENDAR_ID = "family11502398595845783509@group.calendar.google.com"
UNIVERSITY_CALENDAR_ID = "tqthevp9k7nvadm9uamignbmqk@group.calendar.google.com"

# Route slug -> calendar id. Only routes we currently apply.
ROUTE_TO_CALENDAR = {
    "work": WORK_CALENDAR_ID,
    "chiara": CHIARA_CALENDAR_ID,
    "personal": PRIMARY_CALENDAR_ID,
}

# Reverse lookup used by the UI/admin.
CALENDAR_LABEL = {
    PRIMARY_CALENDAR_ID:    "Personale",
    WORK_CALENDAR_ID:       "Work",
    CHIARA_CALENDAR_ID:     "Chiara",
    FAMIGLIA_CALENDAR_ID:   "Famiglia",
    UNIVERSITY_CALENDAR_ID: "University",
}
