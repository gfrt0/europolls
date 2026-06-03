"""Shared utilities for parsing Wikipedia opinion-polling tables.

Designed to mimic and extend the Europepolls (Pitas 2023) preprocessing
recipe, but with deterministic wikitext parsing instead of wikitable2csv.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
BOLD_RE = re.compile(r"'''([^']+)'''")
ITALIC_RE = re.compile(r"''([^']+)''")
EXTLINK_RE = re.compile(r"\[https?://\S+\s+([^\]]+)\]")
EXTLINK_FULL_RE = re.compile(r"\[(https?://\S+)\s+([^\]]+)\]")


REF_CITE_URL_RE = re.compile(
    # Match a citation template inside any <ref>. Covers:
    #   {{cite web | url=... |  ... }}    (most common)
    #   {{Cite news | url=...}}           (alt template)
    #   {{Citation | url=...}}            (DK uses this)
    #   {{webarchive | url=...}}          (BE common, links to web.archive.org)
    # — tolerates whitespace, case-insensitive on the template name and
    # the url= parameter.
    r"<ref[^>]*>[\s\S]*?\{\{\s*(?:cite\s+\w+|citation|webarchive)\b"
    r"[^}]*?\burl\s*=\s*(https?://[^\s|}]+)",
    re.IGNORECASE,
)

REF_BRACKETED_URL_RE = re.compile(
    # `<ref>[https://... optional display text]</ref>` — BE / DK fallback.
    # The URL stops at any whitespace OR the closing bracket so we don't
    # grab `]</ref>` into the captured group.
    r"<ref[^>]*>\s*\[\s*(https?://[^\s\]]+)",
    re.IGNORECASE,
)

REF_BARE_URL_RE = re.compile(
    # `<ref>https://...</ref>` — bare URL inside ref (DK old articles).
    r"<ref[^>]*>\s*(https?://\S+?)(?=[\s<|}\]])",
    re.IGNORECASE,
)


SELF_CLOSING_REF_RE = re.compile(
    r'<ref\s+name\s*=\s*"?([^"\s/>]+)"?\s*/>',
    re.IGNORECASE,
)
NAMED_REF_RE = re.compile(
    r'<ref\s+name\s*=\s*"?([^"\s>]+)"?\s*>([\s\S]+?)</ref>',
    re.IGNORECASE,
)


def build_ref_name_index(article_text: str) -> dict[str, str]:
    """Scan a whole article for named ``<ref name="X">...</ref>`` blocks
    and return ``{name: url}`` so self-closing refs ``<ref name="X"/>``
    can be resolved back to their URL.

    Belgian polling tables (and other articles that repeat the same
    citation across many rows) use the named-ref pattern heavily — without
    this index ~99% of BE rows lose their URL.
    """
    out: dict[str, str] = {}
    for m in NAMED_REF_RE.finditer(article_text):
        name, body = m.group(1), m.group(2)
        for pat in (EXTLINK_FULL_RE, REF_CITE_URL_RE,
                    REF_BRACKETED_URL_RE, REF_BARE_URL_RE):
            inner = pat.search('<ref>' + body + '</ref>') or pat.search(body)
            if inner:
                out[name] = inner.group(1)
                break
    return out


def extract_external_url(cell: str, ref_index: dict[str, str] | None = None) -> str | None:
    """Pull the first external URL from a wikitext cell.

    Tries, in order:
      1. Inline link: ``[https://example.com/poll SWG]`` (covers ~69% of
         poll rows directly in the cell).
      2. ``<ref>{{cite web|url=...}}</ref>`` and variants (cite news /
         citation / webarchive — covers ES / IE / LV / GR / SK / UK).
      3. ``<ref>[https://...]</ref>`` bracketed external link (BE / DK).
      4. ``<ref>https://...</ref>`` bare URL (DK old articles).
      5. ``<ref name="X"/>`` self-closing ref resolved via ``ref_index``
         (BE / NL — pre-built from the full article).

    Returns the URL or None.
    """
    if not cell:
        return None
    for pat in (EXTLINK_FULL_RE, REF_CITE_URL_RE,
                REF_BRACKETED_URL_RE, REF_BARE_URL_RE):
        m = pat.search(cell)
        if m:
            return m.group(1)
    if ref_index:
        m = SELF_CLOSING_REF_RE.search(cell)
        if m and m.group(1) in ref_index:
            return ref_index[m.group(1)]
    return None
STYLE_PREFIX_RE = re.compile(r"^[^|]*\|\s*")  # strip `style="..."|` cell prefix


FILE_LINK_RE = re.compile(
    r"\[\[\s*(?:File|Image)\s*:\s*[^\]|]+(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)


def strip_wikitext(cell: str) -> str:
    """Strip wikitext markup from a cell value, returning plain text.

    Handles wikilinks ``[[A|B]]`` → B, external links, templates, bold/italic,
    HTML tags, and the inline cell-style prefix ``style="..."|``.

    Special-cases image links ``[[File:foo.jpg|...|link=Party]]``: when a
    ``link=X`` parameter is present, the party name X is preferred. Otherwise
    the file link is dropped entirely so size params like ``80x80px`` don't
    leak into output.
    """
    if cell is None:
        return ""
    s = cell.strip()
    # Strip wikitable cell attribute prefixes like `rowspan="4" ` or
    # `style="..." colspan=2 ` that some editors put inline.
    s = re.sub(r'^(?:rowspan|colspan|style|class|align|valign|width)\s*=\s*"[^"]*"\s+', "", s, flags=re.IGNORECASE)
    s = re.sub(r'^(?:rowspan|colspan|style|class|align|valign|width)\s*=\s*\w+\s+', "", s, flags=re.IGNORECASE)
    # Unwrap a few common content-preserving templates (nowrap, small, br, etc.).
    s = re.sub(r"\{\{\s*(?:nowrap|small|sub|sup|abbr|nobr|nbsp)\s*\|\s*([^{}]*?)\s*\}\}",
               r"\1", s, flags=re.IGNORECASE)
    # File/Image links: prefer the LAST `|`-separated parameter that isn't a
    # size spec or a `link=` target — Wikipedia editors often place the party
    # short code in that slot. Example:
    #   [[File:PSOE.svg|25px|link=Spanish Socialist Workers' Party|PSOE]] → PSOE
    # If only `link=X` is present, fall back to X. Otherwise drop the whole link.
    def _file_repl(m: re.Match) -> str:
        body = m.group(0).rstrip("]").lstrip("[")
        parts = [p.strip() for p in body.split("|")]
        # parts[0] is "File:foo.jpg"
        candidates = []
        link_target = None
        for p in parts[1:]:
            if not p:
                continue
            if re.fullmatch(r"\d+\s*x?\s*\d*\s*px", p, re.IGNORECASE):  # size
                continue
            if p.lower() in {"thumb", "frame", "frameless", "border", "center",
                             "left", "right", "none", "upright"}:
                continue
            if p.lower().startswith("link="):
                link_target = p.split("=", 1)[1].strip()
                continue
            if p.lower().startswith("alt="):
                continue
            candidates.append(p)
        if candidates:
            return candidates[-1]
        return link_target or ""
    s = FILE_LINK_RE.sub(_file_repl, s)
    # Drop templates recursively (some are nested 1-deep, which TEMPLATE_RE won't
    # match — iterate until stable).
    for _ in range(5):
        new = TEMPLATE_RE.sub("", s)
        if new == s:
            break
        s = new
    # Wikilinks: keep the display text after the pipe, else the target.
    s = WIKILINK_RE.sub(lambda m: m.group(1).split("|")[-1], s)
    # External links: keep the display text.
    s = EXTLINK_RE.sub(r"\1", s)
    # Bold/italic.
    s = BOLD_RE.sub(r"\1", s)
    s = ITALIC_RE.sub(r"\1", s)
    # HTML tags (br, sup, etc.).
    s = HTML_TAG_RE.sub("", s)
    # Inline style prefix `style="..."|` that sometimes precedes a value.
    s = STYLE_PREFIX_RE.sub("", s) if "|" in s and "style=" in s else s
    return s.strip()


def parse_share(cell: str) -> float | None:
    """Parse a single polling-share cell ('21.5', '21,5%', ''') into float.

    Tolerates a trailing parenthetical seat count ('20.4%(31)' on NL 2010,
    '25.2%(46.9%)¤' on MT 2013, '34.8% (62)' on SK 2012) and a few
    miscellaneous trailing markers (¤, asterisks, footnote daggers).
    """
    s = strip_wikitext(cell)
    # Strip trailing footnote / marker characters FIRST so a closing paren
    # adjacent to one doesn't block the parens-suffix strip below.
    s = re.sub(r"[¤*†‡§]+\s*$", "", s).strip()
    # Drop any '(...)' suffix — Wikipedia tables sometimes append a seat
    # projection or absolute count alongside the percentage. '25.2%(46.9%)'
    # → '25.2%'.
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    s = s.replace(",", ".").replace("%", "").strip()
    if not s or s in {"–", "—", "-", "?", "N/A"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_sample(cell: str) -> int | None:
    """Parse a sample-size cell ('1,200' / '1.200' / '1200')."""
    s = strip_wikitext(cell).replace(",", "").replace(".", "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


@dataclass
class FieldworkDates:
    start: str | None  # ISO YYYY-MM-DD
    end: str | None


MONTHS: dict[str, int] = {}
for i, names in enumerate([
    ("Jan", "January"),
    ("Feb", "February"),
    ("Mar", "March"),
    ("Apr", "April"),
    ("May", "May"),
    ("Jun", "June"),
    ("Jul", "July"),
    ("Aug", "August"),
    ("Sep", "Sept", "September"),       # DE 2009 uses "18 Sept" abbreviation
    ("Oct", "October"),
    ("Nov", "November"),
    ("Dec", "December"),
], start=1):
    for n in names:
        MONTHS[n.lower()] = i


def _to_iso(day: int, month: int, year: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


OPDRTS_RE = re.compile(
    r"\{\{\s*[Oo]pdrts\s*\|\s*(\d{0,2})\s*\|\s*(\d{0,2})\s*\|\s*([A-Za-z]+)\s*\|\s*(\d{4})\s*"
    r"(?:\|\s*\w+\s*)?\}\}"  # optional 5th arg like 'year'
)


def parse_opdrts(cell: str) -> FieldworkDates | None:
    """Handle the ``{{Opdrts|d1|d2|MMM|YYYY}}`` template used on it.wiki and
    en.wiki Italian polling articles. Variants:

    - ``{{Opdrts|D1|D2|MMM|YYYY}}`` — standard range
    - ``{{Opdrts||DD|MMM|YYYY}}`` — single-day poll on DD (empty first param)
    - ``{{Opdrts|DD||MMM|YYYY}}`` — same, other variant
    - ``D1 > D2`` — month boundary (D1 in prev month, D2 in named month)
    """
    m = OPDRTS_RE.search(cell)
    if not m:
        return None
    d1_s, d2_s, mo, y = m[1], m[2], m[3], int(m[4])
    d1 = int(d1_s) if d1_s else None
    d2 = int(d2_s) if d2_s else None
    mn = MONTHS.get(mo.lower())
    if mn is None or (d1 is None and d2 is None):
        return None
    # Single-day cases: collapse to one date.
    if d1 is None:
        return FieldworkDates(_to_iso(d2, mn, y), _to_iso(d2, mn, y))
    if d2 is None:
        return FieldworkDates(_to_iso(d1, mn, y), _to_iso(d1, mn, y))
    if d1 <= d2:
        return FieldworkDates(_to_iso(d1, mn, y), _to_iso(d2, mn, y))
    # month boundary: d1 belongs to the previous month
    prev_m = mn - 1 or 12
    prev_y = y if mn > 1 else y - 1
    return FieldworkDates(_to_iso(d1, prev_m, prev_y), _to_iso(d2, mn, y))


# {{Dts|format=dmy|YYYY|MM|DD|abbr=on}} — Wikipedia's sortable-date template.
# Allow named parameters before AND after the numeric Y/M/D triplet, and accept
# the family of aliases ({{dts}}, {{date table sorting}}, {{dts/yhm}}). The
# template can carry |abbr=, |fmt=, |format=, |hrec=, |y=, |err= etc.
_NAMED_PARAM = r"(?:\|[a-z][a-z0-9_]*=[^|}]*)*"
DTS_RE = re.compile(
    r"\{\{\s*(?:dts|date table sorting|dts/[a-z]+)\s*"
    + _NAMED_PARAM +
    r"\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})\s*"
    + _NAMED_PARAM +
    r"\s*\}\}",
    re.IGNORECASE,
)
DTS_ISO_RE = re.compile(
    r"\{\{\s*(?:dts|date table sorting|dts/[a-z]+)\s*"
    + _NAMED_PARAM +
    r"\s*\|\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*"
    + _NAMED_PARAM +
    r"\s*\}\}",
    re.IGNORECASE,
)


def parse_dts(cell: str) -> FieldworkDates | None:
    """Handle ``{{dts|format=dmy|YYYY|MM|DD}}`` / ``{{dts|YYYY|MM|DD}}`` — Wiki's
    sortable date template used in older polling tables."""
    for rx in (DTS_RE, DTS_ISO_RE):
        m = rx.search(cell)
        if m:
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
            iso = _to_iso(d, mo, y)
            return FieldworkDates(iso, iso)
    return None


def parse_fieldwork(cell: str, default_year: int) -> FieldworkDates:
    """Parse an 'Administered' cell like '17 Aug–9 Sep', '4–9 Sep', '9 Sep 2022'.

    Falls back gracefully and returns FieldworkDates(None, None) on failure.
    """
    # Templates first.
    opd = parse_opdrts(cell)
    if opd is not None:
        return opd
    dts = parse_dts(cell)
    if dts is not None:
        return dts

    s = strip_wikitext(cell)
    if not s:
        return FieldworkDates(None, None)

    # Normalize various Unicode dashes to ASCII hyphen.
    #   – U+2013 en-dash
    #   — U+2014 em-dash
    #   − U+2212 minus sign (LU 2023 TNS uses this)
    #   ‒ U+2012 figure dash
    #   ― U+2015 horizontal bar
    for ch in ("–", "—", "−", "‒", "―"):
        s = s.replace(ch, "-")
    s = s.strip()

    # Case A: "17 Aug-9 Sep" or "17 Aug-9 Sep 2022"  — months on both sides
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s*-\s*(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?$", s)
    if m:
        d1, mo1, d2, mo2, y = m.groups()
        year = int(y) if y else default_year
        m1n, m2n = MONTHS.get(mo1.lower()), MONTHS.get(mo2.lower())
        if m1n and m2n:
            return FieldworkDates(_to_iso(int(d1), m1n, year), _to_iso(int(d2), m2n, year))

    # Case B: "4-9 Sep" or "4-9 Sep 2022"  — single month at the end
    m = re.match(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?$", s)
    if m:
        d1, d2, mo, y = m.groups()
        year = int(y) if y else default_year
        mn = MONTHS.get(mo.lower())
        if mn:
            return FieldworkDates(_to_iso(int(d1), mn, year), _to_iso(int(d2), mn, year))

    # Case C: "9 Sep" or "9 Sep 2022" or "6 Mar 11" — single date with optional
    # 2- or 4-digit year. 2-digit year: <50 → 20YY, ≥50 → 19YY.
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{2,4}))?$", s)
    if m:
        d, mo, y = m.groups()
        year = int(y) if y else default_year
        if year < 100:
            year += 2000 if year < 50 else 1900
        mn = MONTHS.get(mo.lower())
        if mn:
            iso = _to_iso(int(d), mn, year)
            return FieldworkDates(iso, iso)

    # Case C': "September 2009" / "Feb 2015" — month + 4-digit year. Day=15
    # so it sorts mid-month. Unambiguous; accepts full month names.
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{4})$", s)
    if m:
        mo, y = m.groups()
        mn = MONTHS.get(mo.lower())
        if mn:
            iso = _to_iso(15, mn, int(y))
            return FieldworkDates(iso, iso)

    # Case C'': "Jan 11" / "Dec 14" — month abbreviation + 2-digit year suffix.
    # Restricted to 3-4 letter abbreviations because "October 13" is genuinely
    # ambiguous (Oct 13th vs Oct 2013) and old SI/IS articles use the former.
    m = re.match(r"([A-Za-z]{3,4})\.?\s+(\d{2})$", s)
    if m:
        mo, y = m.groups()
        mn = MONTHS.get(mo.lower())
        if mn:
            year = int(y)
            year += 2000 if year < 50 else 1900
            iso = _to_iso(15, mn, year)
            return FieldworkDates(iso, iso)

    # Case C'': "October 13, 2008" — US-style month-day-year. Requires the
    # comma to disambiguate from the month+year reading above (e.g. "Jan 11"
    # which the month+year branch picks up as Jan 2011, not Jan 11th).
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2})\s*,\s*(\d{2,4})$", s)
    if m:
        mo, d, y = m.groups()
        mn = MONTHS.get(mo.lower())
        if mn and int(d) <= 31:
            year = int(y)
            if year < 100:
                year += 2000 if year < 50 else 1900
            iso = _to_iso(int(d), mn, year)
            return FieldworkDates(iso, iso)

    # Case C''': "October 13" / "November 19" / "June 5" — full or 4-letter
    # month name + day, no year. Common on older election-article polling
    # tables where the article's own cycle year is implicit (SI 2011 uses
    # this throughout). Restricted to ≥4 letter spellings so 3-letter
    # abbreviations stay with the 2-digit-year-suffix branch above (which
    # reads 'Jan 11' as Jan 2011). The 4-letter cutoff covers June/July
    # while leaving May ambiguous (rare; if it shows up it'll fall through
    # to the previous 'May 13 → May 2013' year reading).
    m = re.match(r"([A-Za-z]{4,})\s+(\d{1,2})$", s)
    if m:
        mo, d = m.groups()
        mn = MONTHS.get(mo.lower())
        if mn and 1 <= int(d) <= 31:
            iso = _to_iso(int(d), mn, default_year)
            return FieldworkDates(iso, iso)

    # Case D: ISO already.
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        iso = _to_iso(int(m[3]), int(m[2]), int(m[1]))
        return FieldworkDates(iso, iso)

    # Case E: DE/AT style 'DD.MM.YYYY' or 'DD.MM.YY'.
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000 if y < 50 else 1900
        iso = _to_iso(d, mo, y)
        return FieldworkDates(iso, iso)

    # Case F: DE/AT range 'DD.MM.YYYY-DD.MM.YYYY' or '-DD.MM.YYYY'.
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m:
        d1, m1, y1, d2, m2, y2 = (int(m[i]) for i in (1, 2, 3, 4, 5, 6))
        if y1 < 100: y1 += 2000 if y1 < 50 else 1900
        if y2 < 100: y2 += 2000 if y2 < 50 else 1900
        return FieldworkDates(_to_iso(d1, m1, y1), _to_iso(d2, m2, y2))

    # Case F': LU 2013 range 'DD.MM-DD.MM.YYYY' — year on the end-date only.
    m = re.match(r"(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", s)
    if m:
        d1, mo1, d2, mo2, y = (int(m[i]) for i in (1, 2, 3, 4, 5))
        if y < 100: y += 2000 if y < 50 else 1900
        return FieldworkDates(_to_iso(d1, mo1, y), _to_iso(d2, mo2, y))

    # Case G: full date range with explicit years, '29 Dec 2017-2 Jan 2018'.
    m = re.match(
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", s,
    )
    if m:
        d1, mo1, y1, d2, mo2, y2 = m[1], m[2], m[3], m[4], m[5], m[6]
        m1n, m2n = MONTHS.get(mo1.lower()), MONTHS.get(mo2.lower())
        if m1n and m2n:
            return FieldworkDates(
                _to_iso(int(d1), m1n, int(y1)), _to_iso(int(d2), m2n, int(y2)),
            )

    return FieldworkDates(None, None)


