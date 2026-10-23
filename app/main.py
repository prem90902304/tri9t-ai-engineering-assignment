from datetime import datetime, timezone
import difflib, hashlib, json, os
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from app.db import Base, engine, get_db
from app.models import Document, DocumentVersion, SectionNode, Selection, SelectionItem
from app.schemas import SelectionCreate, GenerationRequest
from app.parser import parse_pdf, flatten, sha256_text
from app.store import all_generations, save_generation
from app.llm import generate_cases, LLMError

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Tri9T AI — Versioned CT-200 QA API", version="1.0.0")

def node_dict(n):
    return {"id":n.id,"version_id":n.version_id,"logical_key":n.logical_key,"heading":n.heading,
            "level":n.level,"body_text":n.body_text,"content_hash":n.content_hash,
            "parent_id":n.parent_id,"order_index":n.order_index,"node_type":n.node_type}

def _children(db: Session, node_id: int):
    return db.scalars(
        select(SectionNode).where(SectionNode.parent_id == node_id)
        .order_by(SectionNode.order_index, SectionNode.id)
    ).all()

def _full_text(db: Session, node: SectionNode) -> str:
    """Return the selected node's complete subtree in reading order."""
    parts = [node.heading]
    if node.body_text.strip():
        parts.append(node.body_text.strip())
    for child in _children(db, node.id):
        parts.append(_full_text(db, child))
    return "\n".join(part for part in parts if part)

def _node_tree(db: Session, node: SectionNode) -> dict:
    children = _children(db, node.id)
    return {
        **node_dict(node),
        "full_text": _full_text(db, node),
        "children": [_node_tree(db, child) for child in children],
    }

def _expand_selected_subtrees(db: Session, version_id: int, selected_nodes: list[SectionNode]) -> list[SectionNode]:
    """Capture selected nodes and all descendants once, preserving tree traversal order."""
    all_nodes = db.scalars(
        select(SectionNode).where(SectionNode.version_id == version_id)
    ).all()
    by_parent: dict[int | None, list[SectionNode]] = {}
    by_id = {node.id: node for node in all_nodes}
    for node in all_nodes:
        by_parent.setdefault(node.parent_id, []).append(node)
    for siblings in by_parent.values():
        siblings.sort(key=lambda node: (node.order_index, node.id))

    expanded: list[SectionNode] = []
    seen: set[int] = set()
    def visit(node: SectionNode):
        if node.id in seen:
            return
        seen.add(node.id)
        expanded.append(node)
        for child in by_parent.get(node.id, []):
            visit(child)
    for selected in selected_nodes:
        # Resolve through the version-scoped map, rather than trusting detached request objects.
        node = by_id.get(selected.id)
        if node is not None:
            visit(node)
    return expanded

def latest_version(db, document_id):
    return db.scalar(select(DocumentVersion).where(DocumentVersion.document_id==document_id)
                     .order_by(DocumentVersion.version_number.desc()))

@app.get("/health")
def health():
    return {"status":"ok"}

@app.post("/documents/ingest")
async def ingest(file: UploadFile = File(...), document_name: str = Query("CT-200")):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded PDF is empty")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(400, "Uploaded file does not have a valid PDF signature")
    temp = Path("data/uploads")
    temp.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    path = temp / f"{digest}.pdf"
    path.write_bytes(content)
    try:
        tree = parse_pdf(str(path))
    except Exception as exc:
        raise HTTPException(422, f"PDF extraction failed: {exc}")
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        doc = db.scalar(select(Document).where(Document.name==document_name))
        if not doc:
            doc = Document(name=document_name); db.add(doc); db.flush()
        existing = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id==doc.id,
                                                            DocumentVersion.source_sha256==digest))
        if existing:
            return {"document_id":doc.id,"version_id":existing.id,"version":existing.version_number,"unchanged_upload":True}
        previous = latest_version(db, doc.id)
        version_num = previous.version_number + 1 if previous else 1
        version = DocumentVersion(document_id=doc.id, version_number=version_num,
                                  source_name=file.filename, source_sha256=digest)
        db.add(version); db.flush()
        created = {}
        flattened = flatten(tree)
        # Match logical keys by normalized hierarchy path; keep immutable per-version node rows.
        # A changed body has same logical identity but a different content hash.
        for n, parent_key in flattened:
            parent_id = created.get(parent_key)
            node = SectionNode(version_id=version.id, logical_key=n.logical_key, heading=n.heading,
                               level=n.level, body_text=n.body_text, content_hash=sha256_text(n.heading+"\n"+n.body_text),
                               parent_id=parent_id, order_index=n.order_index, node_type=n.node_type)
            db.add(node); db.flush(); created[n.logical_key] = node.id
        db.commit()
        return {"document_id":doc.id,"version_id":version.id,"version":version_num,
                "node_count":len(flattened),"source_sha256":digest,
                "matching_strategy":"normalized hierarchy path with sibling occurrence suffix"}
    finally:
        db.close()

@app.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).order_by(Document.id)).all()
    return [{"id":d.id,"name":d.name,"latest_version":(latest_version(db,d.id).version_number if latest_version(db,d.id) else None)} for d in docs]

@app.get("/documents/{document_id}/sections")
def top_sections(document_id: int, version: int | None = None, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404,"Document not found")
    latest = latest_version(db, document_id)
    requested_version = version if version is not None else (latest.version_number if latest else 0)
    ver = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == document_id,
        DocumentVersion.version_number == requested_version,
    ))
    if not ver: raise HTTPException(404,"Version not found")
    nodes = db.scalars(select(SectionNode).where(SectionNode.version_id==ver.id, SectionNode.parent_id.is_(None)).order_by(SectionNode.order_index)).all()
    return {"document_id":document_id,"version":ver.version_number,"sections":[node_dict(n) for n in nodes]}

@app.get("/nodes/{node_id}")
def get_node(node_id: int, db: Session = Depends(get_db)):
    node = db.get(SectionNode,node_id)
    if not node: raise HTTPException(404,"Node not found")
    return _node_tree(db, node)

@app.get("/search")
def search_nodes(q: str = Query(min_length=1), document_id: int | None = None, version: int | None = None, db: Session = Depends(get_db)):
    stmt = select(SectionNode).join(DocumentVersion).where(or_(SectionNode.heading.ilike(f"%{q}%"),SectionNode.body_text.ilike(f"%{q}%")))
    if document_id is not None: stmt = stmt.where(DocumentVersion.document_id==document_id)
    if version is not None: stmt = stmt.where(DocumentVersion.version_number==version)
    rows = db.scalars(stmt.order_by(DocumentVersion.version_number.desc(),SectionNode.order_index)).all()
    return [node_dict(n) for n in rows]

@app.get("/nodes/{node_id}/changes")
def node_changes(node_id: int, db: Session = Depends(get_db)):
    node = db.get(SectionNode, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    source_version = db.get(DocumentVersion, node.version_id)
    document = db.get(Document, source_version.document_id)
    versions = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number)
    ).all()
    matches = []
    for version in versions:
        counterpart = db.scalar(select(SectionNode).where(
            SectionNode.version_id == version.id,
            SectionNode.logical_key == node.logical_key,
        ))
        matches.append((version, counterpart))

    latest_version_row = versions[-1] if versions else None
    latest_node = next((candidate for version, candidate in reversed(matches)
                        if latest_version_row and version.id == latest_version_row.id), None)
    source_position = next(i for i, (version, candidate) in enumerate(matches)
                           if version.id == source_version.id and candidate and candidate.id == node.id)
    later = [(version, candidate) for version, candidate in matches[source_position + 1:]]
    prior = node
    diff_lines = []
    change_events = []
    for version, candidate in later:
        if candidate is None:
            change_events.append({"version": version.version_number, "status": "removed_or_unmatched"})
            continue
        changed = candidate.content_hash != prior.content_hash
        if changed:
            diff_lines.extend(list(difflib.unified_diff(
                prior.body_text.splitlines(), candidate.body_text.splitlines(),
                fromfile=f"v{db.get(DocumentVersion, prior.version_id).version_number}",
                tofile=f"v{version.version_number}", lineterm=""
            )))
            change_events.append({"version": version.version_number, "status": "changed",
                                 "node_id": candidate.id, "content_hash": candidate.content_hash})
        else:
            change_events.append({"version": version.version_number, "status": "unchanged",
                                 "node_id": candidate.id, "content_hash": candidate.content_hash})
        prior = candidate

    latest_status = "missing" if latest_node is None else (
        "changed" if latest_node.content_hash != node.content_hash else "unchanged"
    )
    return {
        "node_id": node.id,
        "logical_key": node.logical_key,
        "source_version": source_version.version_number,
        "latest_version": latest_version_row.version_number if latest_version_row else None,
        "changed": latest_status != "unchanged",
        "latest_status": latest_status,
        "versions": [
            {"version": version.version_number,
             "node_id": candidate.id if candidate else None,
             "status": "present" if candidate else "missing",
             "content_hash": candidate.content_hash if candidate else None}
            for version, candidate in matches
        ],
        "change_events": change_events,
        "diff_summary": {"unified_diff": diff_lines[:40],
                         "heading_changed": bool(latest_node and latest_node.heading != node.heading)},
    }

@app.post("/selections")
def create_selection(payload: SelectionCreate, db: Session = Depends(get_db)):
    version = db.get(DocumentVersion,payload.version_id)
    if not version or version.document_id != payload.document_id: raise HTTPException(404,"Document/version pair not found")
    nodes = [db.get(SectionNode,nid) for nid in payload.node_ids]
    if any(n is None or n.version_id != version.id for n in nodes):
        raise HTTPException(400,"Every node must belong to the selected version")
    # Selecting a section means selecting its full subtree. This prevents a parent with an
    # empty body from producing an empty LLM context and makes descendant edits visible to staleness.
    captured_nodes = _expand_selected_subtrees(db, version.id, nodes)
    selection = Selection(name=payload.name,document_id=payload.document_id,version_id=version.id)
    db.add(selection); db.flush()
    for n in captured_nodes:
        db.add(SelectionItem(selection_id=selection.id,node_id=n.id,version_id=version.id,
             content_hash=n.content_hash,heading_snapshot=n.heading,text_snapshot=n.body_text))
    db.commit()
    return {"selection_id":selection.id,"name":selection.name,"document_id":selection.document_id,
            "version_id":selection.version_id,"requested_node_ids":payload.node_ids,
            "captured_node_ids":[n.id for n in captured_nodes],"version_pinned":True}

def _selection_context(db, selection):
    items = db.scalars(
        select(SelectionItem).where(SelectionItem.selection_id == selection.id)
        .order_by(SelectionItem.id)
    ).all()
    return items, "\n\n".join(f"[{x.heading_snapshot}]\n{x.text_snapshot}\n[content_hash={x.content_hash}]" for x in items)

@app.post("/generations")
def create_generation(payload: GenerationRequest, db: Session = Depends(get_db)):
    selection=db.get(Selection,payload.selection_id)
    if not selection: raise HTTPException(404,"Selection not found")
    old=all_generations()
    if not payload.force_new:
        for item in reversed(old):
            if item["selection_id"]==selection.id and item.get("status")=="complete":
                return {**item,"deduplicated":True}
    items, context = _selection_context(db,selection)
    if not context.strip(): raise HTTPException(400,"Selection has no text")
    try: generated=generate_cases(context)
    except LLMError as exc: raise HTTPException(502,str(exc))
    now=datetime.now(timezone.utc).isoformat()
    record={"generation_id":max([x.get("generation_id",0) for x in old] or [0])+1,
            "selection_id":selection.id,"document_id":selection.document_id,"version_id":selection.version_id,
            "created_at":now,"status":"complete","payload":generated,
            "source_nodes":[{"node_id":x.node_id,"version_id":x.version_id,"logical_key":db.get(SectionNode, x.node_id).logical_key,
                             "content_hash":x.content_hash,"heading":x.heading_snapshot,"text":x.text_snapshot} for x in items],
            "source_hash":hashlib.sha256("".join(x.content_hash for x in items).encode()).hexdigest()}
    save_generation(record)
    return {**record,"deduplicated":False}

def _staleness(record, db):
    changed = []
    current_version = latest_version(db, record["document_id"])
    for source in record.get("source_nodes", []):
        source_node = db.get(SectionNode, source.get("node_id"))
        logical_key = source.get("logical_key") or (source_node.logical_key if source_node else None)
        current = None
        if current_version and logical_key:
            current = db.scalar(select(SectionNode).where(
                SectionNode.version_id == current_version.id,
                SectionNode.logical_key == logical_key,
            ))
        if current is None or current.content_hash != source["content_hash"]:
            changed.append({
                "logical_key": logical_key,
                "source_node_id": source.get("node_id"),
                "current_node_id": current.id if current else None,
                "reason": "removed_or_unmatched" if current is None else "content_changed",
                "source_hash": source["content_hash"],
                "current_hash": current.content_hash if current else None,
            })
    return {
        "stale": bool(changed),
        "changed_sources": changed,
        "checked_against_latest_version": True,
        "latest_version": current_version.version_number if current_version else None,
        "limitation": "Hash changes treat wording edits and safety-critical threshold changes equally; human review is required to assess clinical significance.",
    }

@app.get("/generations")
def list_generations(selection_id: int | None = None, node_id: int | None = None, db: Session = Depends(get_db)):
    data=all_generations()
    if selection_id is not None: data=[x for x in data if x["selection_id"]==selection_id]
    if node_id is not None: data=[x for x in data if any(s["node_id"]==node_id for s in x.get("source_nodes",[]))]
    return [{**x,"staleness":_staleness(x,db)} for x in data]

@app.get("/generations/{generation_id}")
def get_generation(generation_id: int, db: Session = Depends(get_db)):
    for x in all_generations():
        if x["generation_id"]==generation_id:
            return {**x,"staleness":_staleness(x,db)}
    raise HTTPException(404,"Generation not found")
