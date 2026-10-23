from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base

def now_utc():
    return datetime.now(timezone.utc)

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    source_name: Mapped[str] = mapped_column(String(500))
    source_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    document: Mapped[Document] = relationship(back_populates="versions")
    nodes: Mapped[list["SectionNode"]] = relationship(back_populates="version", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)

class SectionNode(Base):
    __tablename__ = "section_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    logical_key: Mapped[str] = mapped_column(String(700), index=True)
    heading: Mapped[str] = mapped_column(String(1000))
    level: Mapped[int] = mapped_column(Integer, default=1)
    body_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("section_nodes.id"), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    node_type: Mapped[str] = mapped_column(String(40), default="section")
    version: Mapped["DocumentVersion"] = relationship(back_populates="nodes")
    parent: Mapped["SectionNode | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["SectionNode"]] = relationship(back_populates="parent", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("version_id", "logical_key"),)

class Selection(Base):
    __tablename__ = "selections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(250))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    items: Mapped[list["SelectionItem"]] = relationship(back_populates="selection", cascade="all, delete-orphan")

class SelectionItem(Base):
    __tablename__ = "selection_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("section_nodes.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    heading_snapshot: Mapped[str] = mapped_column(String(1000))
    text_snapshot: Mapped[str] = mapped_column(Text)
    selection: Mapped["Selection"] = relationship(back_populates="items")
