"""CT-200-focused hierarchy reconstruction. PDF text is preferred; OCR is a fallback.
The supplied assignment PDF describes the source manual but does not include the CT-200
manual v1/v2 itself. Validate and tune these rules against those files before submission.
"""
import hashlib, re
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class ParsedNode:
    heading: str
    level: int
    body_text: str = ""
    parent_index: int | None = None
    order_index: int = 0
    node_type: str = "section"
    logical_key: str = ""
    children: list["ParsedNode"] = field(default_factory=list)

def sha256_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def _extract_pages(path: str) -> list[str]:
    try:
        import fitz
        doc = fitz.open(path)
        pages = [p.get_text("text") for p in doc]
        if sum(len(p.strip()) for p in pages) > 100:
            return pages
    except Exception:
        pass
    try:
        from pdf2image import convert_from_path
        import pytesseract
        return [pytesseract.image_to_string(im) for im in convert_from_path(path)]
    except Exception as exc:
        raise RuntimeError("Could not extract text. Install PyMuPDF, or configure Tesseract + Poppler for scanned PDFs.") from exc

# Numbered headings in the supplied manuals are not consistently sequential (for
# example, 3.4 appears before 3.3 and 2.1.1.1 is nested under 2.1). Keep the
# original section number so hierarchy can be inferred from its numeric prefix.
_NUMBERED = re.compile(r"^\s*((?:\d+\.)*\d+\.?)\s+(.{2,150}?)\s*$")
_LIST_ITEM = re.compile(r"^\s*\d+[.)]\s+[A-Z][^:]{0,80}:\s+.*$")

def _heading(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if not line or len(line) > 180 or _LIST_ITEM.match(line):
        return None
    m = _NUMBERED.match(line)
    if m:
        number, title = m.groups()
        parts = [p for p in number.rstrip(".").split(".") if p]
        # A lone trailing dot in "1. Heading" is punctuation, not another level.
        level = len(parts)
        return title.strip(" .:-"), min(max(level, 1), 6), ".".join(parts)
    if line.isupper() and 3 <= len(line) <= 100 and any(c.isalpha() for c in line):
        if len(line.split()) <= 12:
            return line.title(), 1, None
    if re.match(r"^(Appendix|WARNING|CAUTION|NOTE|Table|Figure)\b", line, re.I):
        return line, 1, None
    return None

def parse_pdf(path: str) -> list[ParsedNode]:
    """Extract text and rebuild a hierarchy using section-number prefixes.

    PyMuPDF preserves reading order for these PDFs. Table text is retained in the
    owning section body; row/cell segmentation is intentionally exposed as a
    limitation rather than guessed from flattened text.
    """
    pages = _extract_pages(path)
    lines = []
    for page_no, page in enumerate(pages, 1):
        for raw in page.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line or re.fullmatch(r"\d+", line):
                continue
            lines.append((page_no, line))

    roots: list[ParsedNode] = []
    stack: list[tuple[ParsedNode, str | None]] = []
    current: ParsedNode | None = None
    numbered_nodes: dict[str, ParsedNode] = {}
    preamble: ParsedNode | None = None

    for page_no, line in lines:
        parsed = _heading(line)
        if parsed:
            heading, level, section_number = parsed
            node = ParsedNode(heading=heading, level=level,
                              order_index=len(roots),
                              node_type="section")
            # The title/preamble precedes the first numbered section and must not
            # become the parent of Section 1.
            if section_number:
                parts = section_number.split(".")
                parent_number = ".".join(parts[:-1]) if len(parts) > 1 else None
                parent = numbered_nodes.get(parent_number) if parent_number else None
                # If a parent number is missing, use the nearest preceding numeric
                # ancestor; otherwise retain this section as a root.
                while parent is None and len(parts) > 2:
                    parts = parts[:-1]
                    parent = numbered_nodes.get(".".join(parts))
                if parent is not None:
                    parent.children.append(node)
                else:
                    roots.append(node)
                numbered_nodes[section_number] = node
                stack = []
            else:
                while stack and stack[-1][0].level >= level:
                    stack.pop()
                if stack:
                    stack[-1][0].children.append(node)
                else:
                    roots.append(node)
                stack.append((node, None))
            current = node
            continue

        # Preserve the document title as a preamble but keep it as a sibling of
        # numbered sections, not their parent.
        if current is None:
            if preamble is None:
                preamble = ParsedNode("Document preamble", 1, node_type="preamble")
                roots.insert(0, preamble)
            preamble.body_text = (preamble.body_text + "\n" + line).strip()
        else:
            current.body_text = (current.body_text + "\n" + line).strip()

    def assign_keys(nodes, parent_path=""):
        seen = {}
        for idx, node in enumerate(nodes):
            normalized = re.sub(r"\W+", "-", node.heading.lower()).strip("-") or "untitled"
            seen[normalized] = seen.get(normalized, 0) + 1
            segment = f"{normalized}~{seen[normalized]}"
            node.logical_key = f"{parent_path}/{segment}" if parent_path else segment
            node.order_index = idx
            assign_keys(node.children, node.logical_key)
    # Reconstruct the two tabular regions present in the supplied CT-200 PDFs.
    # Keep the original flattened text in the parent section as a lossless fallback,
    # while adding structured table/row/cell nodes for downstream retrieval.
    _attach_ct200_tables(roots)
    assign_keys(roots)
    return roots


def _attach_ct200_tables(roots: list[ParsedNode]) -> None:
    """Add structured nodes for recognizable specification and error-code tables.

    PDF text extraction may flatten visual table cells into lines. These conservative
    rules target tables identifiable by their headers and retain the original text
    regardless, so a failed reconstruction never discards source content.
    """
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.children)

    def add_table(parent: ParsedNode, title: str, headers: list[str], rows: list[list[str]]):
        if not rows or any(child.node_type == "table" and child.heading == title for child in parent.children):
            return
        table = ParsedNode(title, parent.level + 1, node_type="table", order_index=len(parent.children))
        table.children.append(ParsedNode("Header", parent.level + 2, node_type="table_row",
            order_index=0, children=[ParsedNode(h, parent.level + 3, body_text=h,
            node_type="table_cell", order_index=i) for i, h in enumerate(headers)]))
        for ri, values in enumerate(rows, 1):
            row = ParsedNode(f"Row {ri}", parent.level + 2, node_type="table_row", order_index=ri)
            for ci, value in enumerate(values):
                row.children.append(ParsedNode(f"Column {ci + 1}", parent.level + 3,
                    body_text=value, node_type="table_cell", order_index=ci))
            table.children.append(row)
        parent.children.append(table)

    for node in list(walk(roots)):
        text = [re.sub(r"\s+", " ", line).strip() for line in node.body_text.splitlines()]
        text = [line for line in text if line]
        normalized_heading = node.heading.casefold().replace("ﬁ", "fi").replace("ﬀ", "ff")
        if "general specifications" in normalized_heading and text[:2] == ["Parameter", "Value"]:
            pairs = []
            values = text[2:]
            for i in range(0, len(values) - 1, 2):
                pairs.append([values[i], values[i + 1]])
            add_table(node, "Specifications table", ["Parameter", "Value"], pairs)
        if normalized_heading == "error codes" and text[:3] == ["Code", "Meaning", "Device Behavior"]:
            rows = []
            i = 3
            while i < len(text):
                code = text[i]
                if re.fullmatch(r"E\d+", code):
                    i += 1
                    meaning = []
                    behavior = []
                    # PDF extraction emits each cell's wrapped lines in column order.
                    # Consume until the next error-code token; split the remaining cell
                    # text at known behavior verbs to reconstruct three columns.
                    while i < len(text) and not re.fullmatch(r"E\d+", text[i]):
                        meaning.append(text[i]); i += 1
                    joined = " ".join(meaning)
                    match = re.search(r"(Aborts measurement|Auto-deﬂates|Auto-deflates|Device disables measurement|Displays E6)", joined)
                    if match:
                        rows.append([code, joined[:match.start()].strip(), joined[match.start():].strip()])
                    elif meaning:
                        rows.append([code, joined, ""])
                else:
                    i += 1
            add_table(node, "Error-code table", ["Code", "Meaning", "Device Behavior"], rows)

def flatten(nodes: list[ParsedNode]):
    result = []
    def walk(items, parent_key=None):
        for n in items:
            result.append((n, parent_key))
            walk(n.children, n.logical_key)
    walk(nodes)
    return result
