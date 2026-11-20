from __future__ import annotations
"""Document extraction. Stands in for AWS Textract plus validation.

Reads text and PDF files and pulls out invoice fields. A ValidationError means
the document itself is bad (retrying will not help). Any other exception is
treated as a transient worker failure and the message is retried.
"""
import re
from decimal import Decimal
from pathlib import Path


class ValidationError(Exception):
    pass


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValidationError("PDF support needs the 'pypdf' package")
        try:
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except Exception as e:
            raise ValidationError(f"could not read PDF: {e}")
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def parse_invoice(text: str) -> dict:
    if "SIMULATE_CRASH" in text:
        raise RuntimeError("simulated worker crash")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValidationError("document is empty")

    def find(pattern):
        m = re.search(pattern, text, re.I | re.M)
        return m.group(1).strip() if m else None

    vendor = find(r"^vendor:\s*(.+)$") or lines[0]
    number = find(r"invoice\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Za-z0-9-]+)")
    date = find(r"^date:\s*(\d{4}-\d{2}-\d{2})")
    total_s = find(r"^total:\s*\$?\s*([\d,]+(?:\.\d+)?)\s*$")
    if total_s is None:
        raise ValidationError("no total found")
    total = _money(total_s)

    items = []
    for name, qty, price in re.findall(r"^(.+?)\s+(\d+)\s*x\s*\$?([\d,]+(?:\.\d+)?)\s*$", text, re.M):
        line_total = int(qty) * _money(price)
        items.append({"description": name.strip(), "qty": int(qty), "unit_price": float(_money(price)), "amount": float(line_total)})
    if items:
        summed = sum(Decimal(str(i["amount"])) for i in items)
        if abs(summed - total) > Decimal("0.01"):
            raise ValidationError(f"line items add up to {summed:.2f} but the total says {total:.2f}")

    return {
        "vendor": vendor,
        "invoice_number": number,
        "date": date,
        "line_items": items,
        "total": float(total),
    }


def extract(path: Path) -> dict:
    return parse_invoice(_read_text(path))
