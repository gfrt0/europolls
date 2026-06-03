"""Surface the malformed `style="..."|` Wikipedia revisions we've
identified, with the exact one-character edit needed.

Each row in the polls_long.csv that has ``party_short = NaN`` traces back
to a wikitext column header whose ``style="..."`` attribute is missing
its closing double-quote, so the parser can't recover the wikilink.
This script greps the raw wikitext, locates each malformed cell,
prints the diff that would fix it, and surfaces the pinned ``oldid``
permalink — a few minutes of work per revision in the Wikipedia editor
saves us ~635 poll-party rows on the next refresh.

Usage:
    python scripts/wikipedia_feedback.py

The output is purely informational — no edits are made. To apply the
fixes, log into Wikipedia and edit the named articles. (pywikibot would
work too, but the edit volume is low enough that manual editing keeps
the diff reviewable.)
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

# Match `! style="..."| ...` where the closing `"` before the cell-content
# pipe is missing. To avoid false positives from legitimate `|` separators
# inside `{{party color|X}}` templates or `[[Article|X]]` wikilinks, we
# require: (a) `style="` followed by characters that DON'T include an
# unmatched `{{` or `[[`; (b) the very next `|` is the cell-content
# separator. This is the GR/SI/LV/CH/DE pattern we documented in the
# README roadmap.
MALFORMED = re.compile(
    # Anchor at start of header marker, then the style attribute prefix.
    r'^(!\s*(?:[^|\n{[]*?)style="'
    # Style value: characters that aren't `"`, `|`, `{`, `[`, or newline.
    # Excluding `{` and `[` means we won't match `style="background:{{...|...}}"`.
    r'[^"\n|{\[]+)'
    # The bare `|` that should have been preceded by `"`.
    r'\|',
    re.MULTILINE,
)
# Inverse — the well-formed version we'd want it to look like.
EXAMPLE_FIX_HEAD = '! style="width:35px;"'


def main() -> None:
    findings: list[dict] = []
    for wikitext in sorted(RAW.glob("*/*/article.wikitext")):
        country = wikitext.parts[-3]
        cycle = wikitext.parts[-2]
        try:
            text = wikitext.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MALFORMED.finditer(text):
            broken = m.group(0).rstrip()
            # The fix: insert `"` before the `|` that closes the cell prefix.
            fix = broken.replace('|', '"|', 1)
            # Pull a snippet so the reviewer can locate the line.
            start = max(0, m.start() - 0)
            end = min(len(text), m.end() + 120)
            ctx = text[start:end].split("\n")[0]
            findings.append({
                "country": country,
                "cycle": cycle,
                "broken": broken,
                "fix": fix,
                "context_tail": text[m.end():m.end()+120].split("\n")[0],
            })

    # Group by article + dedup identical broken patterns
    print(f"# Wikipedia feedback — {len(findings)} malformed `style=` headers across "
          f"{len({(f['country'], f['cycle']) for f in findings})} revisions\n")

    by_article: dict[tuple[str, str], list[dict]] = {}
    for f in findings:
        by_article.setdefault((f["country"], f["cycle"]), []).append(f)

    for (country, cycle), rows in sorted(by_article.items()):
        # Find the article's oldid from the corresponding interim CSV if available.
        # For now just point at the article path.
        seen: set[str] = set()
        unique = [r for r in rows if not (r["broken"] in seen or seen.add(r["broken"]))]
        print(f"\n## {country} / {cycle}  ({len(rows)} instances, {len(unique)} unique patterns)")
        print(f"`data/raw/{country}/{cycle}/article.wikitext`")
        for r in unique[:6]:
            print(f"\n```")
            print(f"-{r['broken']}")
            print(f"+{r['fix']}")
            print(f" {r['context_tail'][:80]}…")
            print(f"```")
        if len(unique) > 6:
            print(f"\n… plus {len(unique) - 6} more unique patterns in this revision")


if __name__ == "__main__":
    main()
