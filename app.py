"""
Lab Invoice Renamer
===================
Drag-and-drop PDF invoices, get back a ZIP named:

    YYYY-MM-DD - vendor - amount[ USD].pdf

Extraction is hybrid: deterministic rules for the vendors this lab orders from
repeatedly, with an optional Claude API fallback for anything unrecognised.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import pdfplumber
import streamlit as st

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CLAUDE_MODEL = "claude-haiku-5-5"  # cheap + fast; swap if you like
MAX_CHARS_TO_MODEL = 6000

st.set_page_config(page_title="Invoice Renamer", page_icon="📄", layout="wide")


# --------------------------------------------------------------------------
# PDF reading
# --------------------------------------------------------------------------

def read_pdf_text(data: bytes) -> str:
    """Extract text, repairing PDFs wrapped in MIME/multipart junk."""
    start = data.find(b"%PDF-")
    if start > 0:
        end = data.rfind(b"%%EOF")
        data = data[start:end + 5] if end > start else data[start:]

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]

    text = "\n".join(pages).replace("\x00", "-")
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def to_amount(raw: str) -> Decimal | None:
    """'1,234.56' or European '108,17' -> Decimal."""
    s = raw.strip().replace(" ", "").replace(" ", "")
    if re.fullmatch(r"[\d.]+,\d{2}", s):        # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return Decimal(s)
    except Exception:
        return None


def round_dollars(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def try_patterns(text: str, patterns: list[tuple[str, str]]) -> datetime | None:
    for pattern, fmt in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return datetime.strptime(m.group(1).strip(), fmt)
            except ValueError:
                continue
    return None


def first_amount(text: str, patterns: list[str]) -> Decimal | None:
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            parts = [to_amount(g) for g in m.groups() if g]
            if parts and all(a is not None for a in parts):
                return sum(parts)  # several groups = components to add up
    return None


# --------------------------------------------------------------------------
# Vendor rules
# --------------------------------------------------------------------------

@dataclass
class Vendor:
    slug: str
    marker: str                       # distinctive string in the PDF text
    dates: list[tuple[str, str]]
    totals: list[str] = field(default_factory=list)
    currency: str = "CAD"
    sum_all_totals: bool = False      # PDFs that bundle several invoices


VENDORS: list[Vendor] = [
    Vendor(
        slug="milliporesigma",
        marker="MilliporeSigma",
        dates=[(r"Invoice Date\s+\d+\s+(\d{2}/\d{2}/\d{4})", "%m/%d/%Y"),
               (r"Invoice Date\s+(\d{2}/\d{2}/\d{4})", "%m/%d/%Y")],
        totals=[r"Total Amount Due:?\s*\$?\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="bectondickinson",
        marker="Becton Dickinson",
        dates=[(r"(\d{2}/\d{2}/\d{4})\s+Net Due", "%m/%d/%Y"),
               (r"Date facture\s*:?\s*(\d{2}/\d{2}/\d{4})", "%m/%d/%Y")],
        totals=[r"GRAND TOTAL[/A-Z ]*\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="abcam",
        marker="Original Sales Invoice",
        dates=[(r"Invoice date\s+([A-Z][a-z]{2} \d{1,2}, \d{4})", "%b %d, %Y")],
        totals=[r"Total\s?payable\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="abcam",
        marker="Commercial Invoice",
        dates=[(r"Date:\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", "%d-%b-%Y")],
        totals=[r"Invoice Total\s*CAD\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="fisherscientific",
        marker="Fisher Scientific",
        dates=[(r"([A-Z][a-z]{2} \d{1,2}, \d{4})\s+CREDIT CARD", "%b %d, %Y")],
        totals=[r"TOTAL\s*:\s*\$?\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="uoftmedstore",
        marker="MedStore Receipt",
        dates=[(r"Order Date:\s*(\d{4}-\d{2}-\d{2})", "%Y-%m-%d")],
        totals=[r"Amount\s*\$\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="amazon",
        marker="Invoice / Facture",
        dates=[(r"Date de facturation:\s*(\d{1,2} [A-Z][a-z]+ \d{4})", "%d %B %Y")],
        totals=[r"Total (?:payable|à payer)[^:]*:\s*\$?\s*([\d,]+\.\d{2})"],
        sum_all_totals=True,
    ),
    Vendor(
        slug="ikea",
        marker="IKEA Canada",
        dates=[(r"Invoice Date:\s*(\d{2}/\d{2}/\d{4})", "%d/%m/%Y")],
        totals=[r"Total\s*\$\s*([\d,]+\.\d{2})\s*Paid",
                r"HST \(13 ?%\)\s*\$\s*[\d,]+\.\d{2}\s*Total\s*\$\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="lunananotech",
        marker="Luna Nanotech",
        dates=[(r"Issue Date:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", "%b %d, %Y")],
        totals=[r"Invoice Total\s*CA\$\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="canadiantire",
        marker="CANADIAN TIRE",
        dates=[(r"REG #:\d+\s+(\d{2}/\d{2}/\d{4})", "%m/%d/%Y")],
        # Total is printed "T O T A L"; the lookbehind skips SUBTOTAL.
        totals=[r"(?<!SUB)T O T A L\s*\$\s*([\d,]+\.\d{2})",
                r"VISA TEND\s*\$?\s*([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="polysciences",
        marker="polysciences",
        dates=[(r"(\d{1,2}[A-Z][a-z]{2}\d{4})\s+\d/\d", "%d%b%Y")],
        totals=[r"Amount Paid:?\s*\$?\s*([\d,]+\.\d{2})",
                r"Total Amount:\s*\$?\s*([\d,]+\.\d{2})"],
        currency="USD",
    ),
    Vendor(
        slug="biorender",
        marker="BioRender",
        dates=[(r"Date of issue\s*([A-Z][a-z]+ \d{1,2}, \d{4})", "%B %d, %Y")],
        totals=[r"Amount due\s*\$?\s*([\d,]+\.\d{2})"],
        currency="USD",
    ),
    Vendor(
        slug="wisent",
        marker="Wisent Inc",
        dates=[(r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d")],
        # European decimal comma: "108,17 $"
        totals=[r"Total\s?Amount\s*([\d.,]+,\d{2})\s*\$"],
    ),
    Vendor(
        slug="mcmastercarr",
        marker="McMaster",
        dates=[(r"Invoice Date\s*(\d{1,2}/\d{1,2}/\d{2})\b", "%m/%d/%y")],
        totals=[r"Paid \(US Dollars\)\s*\$\s*([\d,]+\.\d{2})"],
        currency="USD",
    ),
    Vendor(
        slug="uline",
        marker="uline.ca",
        dates=[(r"INVOICE DATE\s+\d+\s+\S+\s+\S+\s+\S+\s+\d{2}/\d{2}/\d{2}\s+\d{2}/\d{2}/\d{2}\s+\S+\s+(\d{2}/\d{2}/\d{2})", "%m/%d/%y"),
               (r"ORDER DATE.*?(\d{2}/\d{2}/\d{2})", "%m/%d/%y")],
        # No grand total printed: sub-total + tax + shipping.
        totals=[r"AMOUNT DUE\s+\S+\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})"],
    ),
    Vendor(
        slug="lab261",
        marker="lab261.com",
        dates=[(r"(?<!SHIP )(?<!DUE )\bDATE\s+(\d{2}/\d{2}/\d{4})", "%m/%d/%Y")],
        totals=[r"\bTOTAL\s+([\d,]+\.\d{2})"],
        currency="USD",   # Palo Alto supplier; invoice doesn't print a currency
    ),
]


def detect_currency(text: str, default: str) -> str:
    if re.search(r"\bUS Dollars\b|\bUSD\b", text):
        return "USD"
    if re.search(r"\bCAD\b|CA\$|GST/HST|TPS/TVH", text):
        return "CAD"
    return default


def extract_rules(text: str) -> dict | None:
    for vendor in VENDORS:
        if vendor.marker.lower() not in text.lower():
            continue

        when = try_patterns(text, vendor.dates)

        if vendor.sum_all_totals:
            # One PDF can hold several invoices; dedupe by invoice number.
            pairs = re.findall(
                r"# de facture:\s*(\S+).*?Total (?:payable|à payer)[^:]*:\s*\$?\s*([\d,]+\.\d{2})",
                text,
            )
            seen: dict[str, Decimal] = {}
            for inv_no, raw in pairs:
                amt = to_amount(raw)
                if amt is not None:
                    seen.setdefault(inv_no, amt)
            total = sum(seen.values()) if seen else first_amount(text, vendor.totals)
            count = max(len(seen), 1)
        else:
            total = first_amount(text, vendor.totals)
            count = 1

        if when and total:
            return {
                "date": when,
                "vendor": vendor.slug,
                "amount": total,
                "currency": detect_currency(text, vendor.currency),
                "source": "rule" if count == 1 else f"rule ({count} invoices summed)",
            }
    return None


# --------------------------------------------------------------------------
# Claude fallback
# --------------------------------------------------------------------------

PROMPT = """Read this invoice or receipt text and return ONLY a JSON object, no prose, no markdown fences:
{"date":"YYYY-MM-DD","vendor":"lowercase-slug","amount":"123.45","currency":"CAD or USD"}

Rules:
- date = the invoice/receipt/order date printed on the document, NOT a due date, print date, or shipment date.
- vendor = the seller's company name, lowercase, no spaces or punctuation (e.g. "fisherscientific", "uoftmedstore"). For an Amazon marketplace invoice the vendor is "amazon", not the third-party seller.
- amount = the grand total actually payable, including tax and shipping.
- currency = CAD or USD. Only say USD if the document says so explicitly.
If the document holds several invoices, sum their totals and use the earliest date.

TEXT:
"""


def extract_claude(text: str, api_key: str, pdf_bytes: bytes | None = None) -> dict | None:
    """Text mode when the PDF has a text layer; otherwise send the PDF itself
    so Claude can read scanned / image-only receipts."""
    import base64
    import json
    from anthropic import Anthropic

    if text:
        content = PROMPT + text[:MAX_CHARS_TO_MODEL]
    else:
        content = [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode()}},
            {"type": "text", "text": PROMPT + "(see attached PDF)"},
        ]

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": content}],
    )
    body = "".join(b.text for b in msg.content if b.type == "text")
    body = re.sub(r"^```(?:json)?|```$", "", body.strip(), flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(body)
        when = datetime.strptime(parsed["date"], "%Y-%m-%d")
        amount = to_amount(str(parsed["amount"]))
        slug = re.sub(r"[^a-z0-9]", "", str(parsed["vendor"]).lower())
    except Exception:
        return None

    if not (when and amount and slug):
        return None

    return {
        "date": when,
        "vendor": slug,
        "amount": amount,
        "currency": "USD" if str(parsed.get("currency", "")).upper() == "USD" else "CAD",
        "source": "claude" if text else "claude (read scan)",
    }


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def build_name(row: dict) -> str:
    suffix = " USD" if row["currency"] == "USD" else ""
    return (
        f"{row['date'].strftime('%Y-%m-%d')} - {row['vendor']} - "
        f"{round_dollars(row['amount'])}{suffix}.pdf"
    )


def dedupe(names: list[str]) -> list[str]:
    """Append (1), (2)… to names that would otherwise collide."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1

    running: dict[str, int] = {}
    out = []
    for name in names:
        if counts[name] == 1:
            out.append(name)
        else:
            running[name] = running.get(name, 0) + 1
            stem = name[:-4]
            out.append(f"{stem} ({running[name]}).pdf")
    return out


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

st.title("📄 Invoice Renamer")
st.caption(
    "Drop in invoice PDFs. Files come back as "
    "`YYYY-MM-DD - vendor - amount.pdf`, with ` USD` appended for US-dollar invoices."
)

try:
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
except Exception:
    api_key = ""  # no secrets file configured — rules-only mode

with st.sidebar:
    st.subheader("Extraction")
    st.write(f"**{len({v.slug for v in VENDORS})} vendors** have hard-coded rules.")
    st.write(", ".join(sorted({v.slug for v in VENDORS})))
    use_claude = st.toggle(
        "Use Claude for unrecognised vendors",
        value=bool(api_key),
        disabled=not api_key,
        help="Needs ANTHROPIC_API_KEY in the app's secrets.",
    )
    if not api_key:
        st.info("No API key configured — unknown vendors will be flagged for manual entry.")

uploads = st.file_uploader(
    "Drop PDFs here", type="pdf", accept_multiple_files=True,
)

if not uploads:
    st.stop()

rows = []
problems = []
progress = st.progress(0.0, text="Reading…")

for i, upload in enumerate(uploads, start=1):
    progress.progress(i / len(uploads), text=f"Reading {upload.name}")
    data = upload.getvalue()

    try:
        text = read_pdf_text(data)
    except Exception as exc:
        problems.append(f"**{upload.name}** — could not be read ({type(exc).__name__}). "
                        "If it's a scan, this app can't OCR it.")
        continue

    result = extract_rules(text) if text else None

    if result is None and use_claude and api_key:
        try:
            # MIME-wrapped files are trimmed so Claude gets a clean PDF.
            start = data.find(b"%PDF-")
            result = extract_claude(text, api_key, data[max(start, 0):])
        except Exception as exc:
            problems.append(f"**{upload.name}** — Claude call failed ({type(exc).__name__}).")

    if result is None:
        why = "no text layer (scan)" if not text else "unrecognised vendor"
        problems.append(f"**{upload.name}** — {why}; fill the row in manually below.")
        result = {
            "date": datetime.today(), "vendor": "UNKNOWN",
            "amount": Decimal("0"), "currency": "CAD", "source": "manual",
        }

    result["original"] = upload.name
    result["bytes"] = data
    rows.append(result)

progress.empty()

if problems:
    with st.expander(f"⚠️ {len(problems)} file(s) need a look", expanded=True):
        for p in problems:
            st.markdown(f"- {p}")

if not rows:
    st.stop()

st.subheader("Review before downloading")
st.caption("Edit any cell that looks wrong — the ZIP is built from this table.")

editor_rows = [
    {
        "Original": r["original"],
        "Date": r["date"].date(),
        "Vendor": r["vendor"],
        "Amount": float(r["amount"]),
        "USD": r["currency"] == "USD",
        "Via": r["source"],
    }
    for r in rows
]

edited = st.data_editor(
    editor_rows,
    width="stretch",
    hide_index=True,
    disabled=["Original", "Via"],
    column_config={
        "Amount": st.column_config.NumberColumn(format="%.2f"),
        "USD": st.column_config.CheckboxColumn(help="Tick if the invoice is in US dollars"),
        "Via": st.column_config.TextColumn(help="How the fields were extracted"),
    },
    key="review",
)

final = [
    {
        "date": datetime.combine(row["Date"], datetime.min.time()),
        "vendor": re.sub(r"[^a-z0-9]", "", str(row["Vendor"]).lower()) or "unknown",
        "amount": Decimal(str(row["Amount"])),
        "currency": "USD" if row["USD"] else "CAD",
    }
    for row in edited
]

names = dedupe([build_name(r) for r in final])

st.subheader("New filenames")
for old, new in zip((r["original"] for r in rows), names):
    st.text(f"{old}  →  {new}")

buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
    for row, name in zip(rows, names):
        zf.writestr(name, row["bytes"])

st.download_button(
    "⬇️  Download renamed ZIP",
    data=buffer.getvalue(),
    file_name=f"invoices-renamed-{datetime.today():%Y-%m-%d}.zip",
    mime="application/zip",
    type="primary",
)

st.caption(
    "Files are held in memory for this session only — nothing is written to disk or "
    "kept after you close the tab."
)
