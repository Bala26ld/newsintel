"""Document text extraction with stable offsets.

The text stored in `documents.text` is the single string every claim span indexes into. HTML is
flattened with selectolax (scripts and styles dropped, block elements separated by newlines);
plain text is used as is. The 8-K metadata parser reads items, registrant and the period of
report from the text itself so the pipeline does not depend on the adapter's hints alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from newsintel.core.dates import find_dates


@dataclass
class ParsedDocument:
    text: str
    mime: str
    parse_method: str
    meta: dict = field(default_factory=dict)


def html_to_text(html: bytes | str) -> str:
    tree = HTMLParser(html)
    for node in tree.css("script, style, head"):
        node.decompose()
    root = tree.body or tree.root
    if root is None:
        return ""
    text = root.text(separator="\n", strip=False)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def parse_document(body: bytes, mime: str) -> ParsedDocument:
    if "html" in mime or body.lstrip()[:1] == b"<":
        text = html_to_text(body)
        method = "selectolax"
    else:
        text = body.decode("utf-8", errors="replace")
        method = "plain"
    return ParsedDocument(text=text, mime=mime, parse_method=method)


ITEM_RE = re.compile(r"Item\s+(\d\.\d{2})\b[.\s:]*([^\n]{0,120})", re.IGNORECASE)
REGISTRANT_RE = re.compile(r"\n([^\n]{3,120})\n\s*\(Exact name of registrant", re.IGNORECASE)
PERIOD_RE = re.compile(r"Date of Report \(Date of earliest event reported\)[^\n]*?:?\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
CIK_RE = re.compile(r"Central Index Key[^\d]*(\d{4,10})", re.IGNORECASE)


def parse_8k_meta(text: str) -> dict:
    items: list[str] = []
    item_titles: dict[str, str] = {}
    for m in ITEM_RE.finditer(text):
        code = m.group(1)
        if code not in items:
            items.append(code)
            item_titles[code] = m.group(2).strip()
    meta: dict = {"items": items, "item_titles": item_titles}
    r = REGISTRANT_RE.search(text)
    if r:
        meta["registrant"] = r.group(1).strip()
    p = PERIOD_RE.search(text)
    if p:
        ds = find_dates(p.group(1))
        if ds:
            meta["period_of_report"] = ds[0].value.isoformat()
    c = CIK_RE.search(text)
    if c:
        meta["cik"] = c.group(1)
    return meta


def item_section(text: str, item_code: str) -> tuple[int, int]:
    """Character range of an 8-K item's body: from its heading to the next 'Item x.xx' or SIGNATURE."""
    start_m = re.search(rf"Item\s+{re.escape(item_code)}\b", text, re.IGNORECASE)
    if not start_m:
        return (0, len(text))
    start = start_m.start()
    nxt = re.search(r"\n\s*(Item\s+\d\.\d{2}\b|SIGNATURES?\b)", text[start_m.end() :], re.IGNORECASE)
    end = start_m.end() + nxt.start() if nxt else len(text)
    return (start, end)


def first_date_after(text: str, start: int, end: int) -> tuple[date, tuple[int, int]] | None:
    for d in find_dates(text[start:end]):
        return d.value, (start + d.span[0], start + d.span[1])
    return None
