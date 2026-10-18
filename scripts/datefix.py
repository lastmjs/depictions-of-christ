"""Parse free-text artwork dates ("15th century", "c. 1450–60", "1450s", "late 16th c.") into year ranges."""
import re

ORD = r"(\d{1,2})(?:st|nd|rd|th)"


def _century(n, qual=""):
    lo, hi = (n - 1) * 100 + 1, n * 100
    q = qual.lower()
    if "early" in q or "first half" in q or "1st half" in q:
        hi = lo + 49 if "half" in q else lo + 33
    elif "late" in q or "second half" in q or "2nd half" in q:
        lo = hi - 49 if "half" in q else hi - 33
    elif "mid" in q:
        lo, hi = lo + 33, hi - 33
    elif "first quarter" in q:
        hi = lo + 24
    elif "last quarter" in q or "fourth quarter" in q:
        lo = hi - 24
    return lo, hi


PART = re.compile(
    r"\b(1st|2nd|3rd|4th|first|second|third|fourth|last)\s+(quarter|half|third)(?:\s+of)?(?:\s+the)?\s+(\d{1,2})(?:st|nd|rd|th)\b",
    re.I)
PART_INDEX = {"1st": 0, "first": 0, "2nd": 1, "second": 1, "3rd": 2, "third": 2, "4th": 3, "fourth": 3}


def _parts(t):
    """Ranges for phrases like '4th quarter 14th century', '2nd half of the 15th century'."""
    out = []
    for m in PART.finditer(t):
        which, unit, cent = m.group(1).lower(), m.group(2).lower(), int(m.group(3))
        size = {"quarter": 25, "half": 50, "third": 33}[unit]
        n = {"quarter": 4, "half": 2, "third": 3}[unit]
        i = n - 1 if which == "last" else min(PART_INDEX.get(which, 0), n - 1)
        start = (cent - 1) * 100 + 1 + i * size
        out.append((start, min(cent * 100, start + size - 1)))
    return out


def parse_date(text):
    """Return (lo, hi) year range implied by a display date, or None if it cannot be read."""
    if not text:
        return None
    t = text.replace("–", "-").replace("—", "-")
    parts = _parts(t)
    if parts and not re.search(r"(?<!\d)\d{3,4}(?!\d)", t):
        return min(p[0] for p in parts), max(p[1] for p in parts)
    t = PART.sub(lambda m: f"{m.group(3)}th", t) if parts else t
    # catalogue shorthand: "184-" / "184?" = the 1840s, "18--" = the 19th century
    t = re.sub(r"(?<!\d)(1\d)--(?![\d-])", lambda m: f"{m.group(1)}00s", t)
    t = re.sub(r"(?<!\d)(1\d\d)[-?_](?![\d])", lambda m: f"{m.group(1)}0s", t)
    bc = re.search(r"\b(B\.?C\.?E?|BCE)\b", t)
    if bc:
        return None
    years = [int(y) for y in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", t)]
    years = [y for y in years if 100 <= y <= 2030]
    if years:
        lo, hi = min(years), max(years)
        # "1450-55" style abbreviated ranges
        m = re.search(r"(?<!\d)(\d{3,4})\s*-\s*(\d{1,2})(?!\d)", t)
        if m and len(years) == 1:
            a, b = m.group(1), m.group(2)
            end = int(a[: len(a) - len(b)] + b)
            return (lo, end) if end >= lo else (lo, lo)
        if len(years) == 1:
            if re.search(r"(?<!\d)\d{1,2}00s\b", t):
                hi = lo + 99          # "1300s" = the fourteenth century
            elif re.search(r"(?<!\d)\d{2,3}0s\b", t):
                hi = lo + 9           # "1470s"
        return lo, hi
    # centuries, possibly a range of centuries: "3rd-12th century", "late 15th or early 16th century"
    cents = list(re.finditer(r"((?:early|late|mid|middle|first half(?: of)?(?: the)?|second half(?: of)?(?: the)?|1st half(?: of)?(?: the)?|2nd half(?: of)?(?: the)?|first quarter(?: of)?(?: the)?|last quarter(?: of)?(?: the)?|fourth quarter(?: of)?(?: the)?)[\s-]*)?" + ORD, t, re.I))
    if cents and re.search(r"cent|c\.|jh|jahrhundert|siècle|secolo|siglo|eeuw", t, re.I):
        rngs = [_century(int(m.group(2)), m.group(1) or "") for m in cents if 1 <= int(m.group(2)) <= 21]
        if rngs:
            return min(r[0] for r in rngs), max(r[1] for r in rngs)
    return None


def sort_year(text, fallback=None):
    """A single year for sorting: midpoint of the parsed range."""
    p = parse_date(text)
    if not p:
        return fallback
    return (p[0] + p[1]) // 2
