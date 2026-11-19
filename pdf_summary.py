# pdf_summary.py — a plain one-or-more-page text PDF, written by hand (no extra dependency).
# Lines starting with "# " are bold headings; long lines wrap.
import textwrap

_REPLACE = {"—": "-", "–": "-", "−": "-", "≥": ">=", "≤": "<=", "→": "->", "×": "x", "’": "'", "“": '"', "”": '"'}


def _clean(s: str) -> str:
    for a, b in _REPLACE.items():
        s = s.replace(a, b)
    s = s.encode("latin-1", errors="replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def summary_pdf(lines: list[str]) -> bytes:
    width, height, margin, leading = 612, 792, 54, 15
    per_page = int((height - 2 * margin) / leading)

    rendered = []                                   # (font, text) after wrapping
    for line in lines:
        bold = line.startswith("# ")
        text = line[2:] if bold else line
        if bold and rendered:
            rendered.append(("F1", ""))             # blank line before a heading
        for part in textwrap.wrap(text, 92) or [""]:
            rendered.append(("F2" if bold else "F1", part))
    pages = [rendered[i:i + per_page] for i in range(0, len(rendered), per_page)] or [[]]

    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for page in pages:
        ops = [f"BT {leading} TL {margin} {height - margin} Td"]
        for font, text in page:
            ops.append(f"/{font} {12 if font == 'F2' else 10} Tf ({_clean(text)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_no = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                    f"/Contents {content_no} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"

    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        body = body if isinstance(body, bytes) else body.encode("latin-1")
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out
