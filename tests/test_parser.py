from app.parser import ParsedNode, flatten, sha256_text

def test_duplicate_headings_can_have_distinct_logical_keys():
    # Regression fixture for repeated headings under different parents.
    a=ParsedNode("Safety",1); b=ParsedNode("Limits",2); c=ParsedNode("Safety",1); d=ParsedNode("Limits",2)
    a.children.append(b); c.children.append(d)
    def assign(nodes, parent=""):
        seen={}
        for n in nodes:
            base=n.heading.lower()
            seen[base]=seen.get(base,0)+1
            n.logical_key=(parent+"/" if parent else "")+f"{base}~{seen[base]}"
            assign(n.children,n.logical_key)
    assign([a,c])
    assert a.children[0].logical_key != c.children[0].logical_key
    assert a.children[0].logical_key.startswith("safety~1/")
    assert c.children[0].logical_key.startswith("safety~2/")

def test_content_hash_normalizes_whitespace():
    assert sha256_text("Pressure   must be safe") == sha256_text("Pressure\nmust be safe")

def test_flatten_preserves_parent_relationships():
    parent=ParsedNode("Section",1,logical_key="section~1") if False else ParsedNode("Section",1)
    child=ParsedNode("Subsection",2)
    parent.children.append(child)
    parent.logical_key="section~1"; child.logical_key="section~1/subsection~1"
    rows=flatten([parent])
    assert rows[0][1] is None
    assert rows[1][1] == "section~1"

def test_changed_body_changes_hash():
    assert sha256_text("Auto-deflate in 2 seconds") != sha256_text("Auto-deflate in 3 seconds")


def _make_pdf(tmp_path, text):
    import fitz
    path = tmp_path / "parser-fixture.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox((50, 50, 550, 760), text, fontsize=11)
    doc.save(path)
    doc.close()
    return str(path)


def test_pdf_parser_preserves_preamble_and_numbered_parent_child_tree(tmp_path):
    from app.parser import parse_pdf
    text = ("Introductory material before numbered sections. This must not disappear.\n"
            "1 Safety\nGeneral safety information for the monitor.\n"
            "1.1 General precautions\nUse the monitor according to the instructions provided.\n"
            "2 Maintenance\nKeep the monitor clean and dry during storage.\n")
    roots = parse_pdf(_make_pdf(tmp_path, text))
    assert roots[0].heading == "Document preamble"
    assert "Introductory material" in roots[0].body_text
    safety = next(n for n in roots if n.heading == "Safety")
    maintenance = next(n for n in roots if n.heading == "Maintenance")
    assert safety.children[0].heading == "General precautions"
    assert safety.children[0].level == 2
    assert maintenance.level == 1


def test_pdf_parser_keeps_duplicate_headings_distinct_by_parent(tmp_path):
    from app.parser import parse_pdf
    text = ("1 Safety\nSafety details and general precautions.\n"
            "1.1 General precautions\nUse only approved accessories with the monitor.\n"
            "2 Maintenance\nMaintenance instructions and storage guidance.\n"
            "2.1 General precautions\nDisconnect the monitor before cleaning its exterior.\n")
    roots = parse_pdf(_make_pdf(tmp_path, text))
    safety = next(n for n in roots if n.heading == "Safety")
    maintenance = next(n for n in roots if n.heading == "Maintenance")
    safety_child = next(n for n in safety.children if n.heading == "General precautions")
    maintenance_child = next(n for n in maintenance.children if n.heading == "General precautions")
    assert safety_child.logical_key != maintenance_child.logical_key
    assert safety_child.logical_key.startswith("safety~1/")
    assert maintenance_child.logical_key.startswith("maintenance~1/")


def test_pdf_parser_retains_warning_and_caption_like_labels(tmp_path):
    from app.parser import parse_pdf
    text = ("1 Instructions\nFollow the monitor instructions carefully.\n"
            "WARNING: Do not use a damaged cuff.\n"
            "Figure 1: Example monitor display and control buttons.\n")
    roots = parse_pdf(_make_pdf(tmp_path, text))
    headings = [n.heading for n in roots]
    assert any(h.startswith("WARNING:") for h in headings)
    assert any(h.startswith("Figure 1:") for h in headings)


def test_real_ct200_manuals_extract_expected_version_differences():
    """Regression test against the supplied PDFs, not synthetic-only fixtures."""
    from pathlib import Path
    from app.parser import parse_pdf, flatten
    root = Path(__file__).resolve().parents[1] / "data" / "manuals"
    v1 = flatten(parse_pdf(str(root / "ct200_manual_v1.pdf")))
    v2 = flatten(parse_pdf(str(root / "ct200_manual_v2.pdf")))
    by_heading_v1 = {n.heading: n for n, _ in v1}
    by_heading_v2 = {n.heading: n for n, _ in v2}

    assert "Data Export" not in by_heading_v1
    assert "Data Export" in by_heading_v2
    assert "300 measurement cycles" in by_heading_v1["Battery Life Under Typical Use"].body_text
    assert "250 measurement cycles" in by_heading_v2["Battery Life Under Typical Use"].body_text
    assert "below 15%" in by_heading_v1["Battery Life Under Typical Use"].body_text
    assert "below 10%" in by_heading_v2["Battery Life Under Typical Use"].body_text
    assert "40 mmHg increments" in by_heading_v1["Cuﬀ Inﬂation Sequence"].body_text
    assert "30 mmHg increments" in by_heading_v2["Cuﬀ Inﬂation Sequence"].body_text
    alarm_v1 = next(n for n, _ in v1 if n.heading == "Error Codes" and "Auto-deﬂates" in n.body_text)
    alarm_v2 = next(n for n, _ in v2 if n.heading == "Error Codes" and "Auto-deﬂates" in n.body_text)
    assert "within 2 seconds" in alarm_v1.body_text
    assert "within 1.5 seconds" in alarm_v2.body_text
    assert "E6" not in alarm_v1.body_text
    assert "E6" in alarm_v2.body_text

    # Ensure numbered sections are actual roots/parents, not children of the title.
    root_titles = {n.heading for n in parse_pdf(str(root / "ct200_manual_v1.pdf"))}
    assert "Device Overview" in root_titles
    assert "Document preamble" in root_titles


def test_real_manuals_create_structured_table_row_and_cell_nodes():
    from pathlib import Path
    from app.parser import parse_pdf, flatten
    root = Path(__file__).resolve().parents[1] / "data" / "manuals"
    nodes = [n for n, _ in flatten(parse_pdf(str(root / "ct200_manual_v2.pdf")))]
    tables = [n for n in nodes if n.node_type == "table"]
    assert any(n.heading == "Specifications table" for n in tables)
    error_table = next(n for n in tables if n.heading == "Error-code table")
    rows = [n for n in error_table.children if n.node_type == "table_row"]
    assert len(rows) == 7  # header plus E1-E6
    assert any(n.node_type == "table_cell" and n.body_text == "E6" for n, _ in flatten([error_table]))
    # The original flattened text remains available as a fallback.
    error_section = next(n for n in nodes if n.heading == "Error Codes" and "E6" in n.body_text)
    assert "Bluetooth sync failure" in error_section.body_text
