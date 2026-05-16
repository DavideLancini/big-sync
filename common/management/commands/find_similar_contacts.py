"""Pre-flight: list contacts that might be the same person.

Read-only. Groups contacts whose names match exactly (after lowercase +
whitespace collapse) and, with --fuzzy, also groups whose names are within
Levenshtein distance 2 of each other. For each group, prints the full set
of useful fields so the user can decide what to merge by hand.

Examples:
  python manage.py find_similar_contacts
  python manage.py find_similar_contacts --fuzzy
  python manage.py find_similar_contacts --fuzzy --min-len 5
"""
import re
from collections import defaultdict

from django.core.management.base import BaseCommand

from common.models import Contact

FUZZY_MAX_DIST = 2  # default; overridden by --max-dist


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _edit_distance(a: str, b: str, max_dist: int = FUZZY_MAX_DIST) -> int:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[lb]


def _format_contact(c: Contact) -> str:
    parts = [f"id={c.pk}", f'"{c.name}"']
    if c.phones:
        parts.append(f"tel={c.phones}")
    if c.emails:
        parts.append(f"mail={c.emails}")
    if c.company:
        parts.append(f"company={c.company!r}")
    if c.aliases:
        parts.append(f"aliases={c.aliases}")
    if c.notes_url:
        parts.append("drive=YES")
    return "  " + "  ".join(parts)


class Command(BaseCommand):
    help = "List contacts likely to be the same person — read-only, you decide what to merge"

    def add_arguments(self, parser):
        parser.add_argument("--fuzzy", action="store_true",
                            help="Also group names within Levenshtein ≤ --max-dist")
        parser.add_argument("--max-dist", type=int, default=2,
                            help="Max Levenshtein distance for fuzzy grouping (default 2)")
        parser.add_argument("--min-len", type=int, default=4,
                            help="Minimum name length for fuzzy grouping (default 4)")
        parser.add_argument("--include-empty", action="store_true",
                            help="Include contacts with empty name (default: skip)")

    def handle(self, *args, **opts):
        qs = Contact.objects.filter(merged_into__isnull=True)
        if not opts["include_empty"]:
            qs = qs.exclude(name="")

        contacts = list(qs.order_by("name"))
        self.stdout.write(f"Contatti attivi analizzati: {len(contacts)}")

        # Exact groups
        by_norm: dict[str, list[Contact]] = defaultdict(list)
        for c in contacts:
            by_norm[_norm(c.name)].append(c)

        exact_groups = [(k, v) for k, v in by_norm.items() if len(v) > 1]
        exact_groups.sort(key=lambda kv: (-len(kv[1]), kv[0]))

        self.stdout.write(self.style.NOTICE(
            f"\n=== {len(exact_groups)} gruppi con nome ESATTO uguale ==="
        ))
        for norm, group in exact_groups:
            self.stdout.write(f"\n[\"{norm}\"]  ({len(group)} contatti)")
            for c in group:
                self.stdout.write(_format_contact(c))

        if not opts["fuzzy"]:
            self.stdout.write(self.style.WARNING(
                "\n(esegui con --fuzzy per anche cercare nomi quasi-uguali, Levenshtein ≤ 2)"
            ))
            return

        # Fuzzy groups: greedy union-find over contacts not already in an exact group
        in_exact = set()
        for _, group in exact_groups:
            for c in group:
                in_exact.add(c.pk)

        remaining = [c for c in contacts if c.pk not in in_exact]
        max_dist = opts["max_dist"]
        self.stdout.write(self.style.NOTICE(
            f"\n=== Ricerca fuzzy (Levenshtein ≤ {max_dist}) "
            f"su {len(remaining)} contatti restanti ==="
        ))

        parent = {c.pk: c.pk for c in remaining}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # Bucket by first letter so we don't compare all pairs.
        bucket: dict[str, list[Contact]] = defaultdict(list)
        for c in remaining:
            n = _norm(c.name)
            if len(n) >= opts["min_len"]:
                bucket[n[0]].append(c)

        for letter, items in bucket.items():
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    na, nb = _norm(a.name), _norm(b.name)
                    if min(len(na), len(nb)) < opts["min_len"]:
                        continue
                    if _edit_distance(na, nb, max_dist) <= max_dist:
                        union(a.pk, b.pk)

        groups: dict[int, list[Contact]] = defaultdict(list)
        by_id = {c.pk: c for c in remaining}
        for c in remaining:
            groups[find(c.pk)].append(c)

        fuzzy_groups = [g for g in groups.values() if len(g) > 1]
        fuzzy_groups.sort(key=lambda g: (-len(g), _norm(g[0].name)))

        self.stdout.write(f"\nGruppi fuzzy trovati: {len(fuzzy_groups)}")
        for group in fuzzy_groups:
            names = sorted({_norm(c.name) for c in group})
            self.stdout.write(f"\n[{ ' | '.join(names) }]  ({len(group)} contatti)")
            for c in sorted(group, key=lambda x: x.name):
                self.stdout.write(_format_contact(c))
