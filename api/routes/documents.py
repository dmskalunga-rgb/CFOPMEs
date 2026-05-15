#!/usr/bin/env python3
"""
api/routes/documents.py

Enterprise-grade Documents API Router.

Objetivo:
- Expor endpoints HTTP para gestão lógica de documentos em APIs enterprise.
- Suportar ingestão por payload, indexação em memória, busca textual, metadados, tags, classificação,
  versionamento lógico, auditoria leve e exportação segura.
- Aplicar validação Pydantic, autenticação por scopes, request-id e respostas padronizadas.

Endpoints:
    GET    /documents/health
    POST   /documents/ingest
    POST   /documents/search
    GET    /documents/{document_id}
    DELETE /documents/{document_id}
    POST   /documents/{document_id}/classify
    POST   /documents/{document_id}/versions
    GET    /documents/{document_id}/audit
    GET    /documents/stats/summary

Integração:
    from fastapi import FastAPI
    from api.routes.documents import router as documents_router

    app.include_router(documents_router, prefix="/v1")

Notas:
- Este router usa um repositório em memória para ser plug-and-play.
- Em produção, substitua DocumentRepository por banco, object storage, vector DB ou serviço documental.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

try:
    from api.auth.dependencies import CurrentUser, get_current_user, require_scopes
except Exception:  # pragma: no cover
    CurrentUser = Any  # type: ignore

    async def get_current_user() -> Any:  # type: ignore
        return {"subject": "auth-unavailable"}

    def require_scopes(*_: str, **__: Any) -> Any:  # type: ignore
        async def dependency() -> Any:
            return None

        return dependency


LOGGER = logging.getLogger(__name__)
ROUTER_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc
MAX_TEXT_LENGTH = 2_000_000
MAX_CONTENT_BYTES = 10_000_000

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    QUARANTINED = "quarantined"


class DocumentSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class SearchMode(str, Enum):
    KEYWORD = "keyword"
    EXACT = "exact"
    PREFIX = "prefix"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class DocumentMetadata(BaseModel):
    title: Optional[str] = None
    source: Optional[str] = None
    owner: Optional[str] = None
    department: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    sensitivity: DocumentSensitivity = DocumentSensitivity.INTERNAL
    tags: List[str] = Field(default_factory=list)
    custom: Dict[str, Any] = Field(default_factory=dict)


class DocumentIngestRequest(BaseModel):
    document_id: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    text: Optional[str] = None
    content_base64: Optional[str] = None
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    status: DocumentStatus = DocumentStatus.ACTIVE
    tenant_id: Optional[str] = None

    @validator("text")
    def validate_text_size(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"text excede limite de {MAX_TEXT_LENGTH} caracteres")
        return value


class DocumentUpdateVersionRequest(BaseModel):
    text: Optional[str] = None
    content_base64: Optional[str] = None
    change_reason: str = "manual_update"
    metadata_patch: Dict[str, Any] = Field(default_factory=dict)


class DocumentSearchRequest(BaseModel):
    query: str = ""
    mode: SearchMode = SearchMode.KEYWORD
    filters: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    include_archived: bool = False
    limit: int = Field(default=25, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    sort_by: str = "updated_at"
    sort_direction: SortDirection = SortDirection.DESC


class DocumentClassifyRequest(BaseModel):
    rules: Dict[str, List[str]] = Field(default_factory=dict)
    update_metadata: bool = False


class DocumentRecordResponse(BaseModel):
    document_id: str
    version: int
    filename: Optional[str]
    mime_type: str
    title: Optional[str]
    status: DocumentStatus
    sensitivity: DocumentSensitivity
    tenant_id: Optional[str]
    owner: Optional[str]
    department: Optional[str]
    category: Optional[str]
    language: Optional[str]
    tags: List[str]
    text_preview: str
    text_length: int
    content_hash: str
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentIngestResponse(BaseModel):
    request_id: str
    status: str
    document: DocumentRecordResponse
    warnings: List[str] = Field(default_factory=list)


class DocumentSearchHit(BaseModel):
    document: DocumentRecordResponse
    score: float
    highlights: List[str] = Field(default_factory=list)


class DocumentSearchResponse(BaseModel):
    request_id: str
    status: str
    total: int
    returned: int
    latency_ms: float
    hits: List[DocumentSearchHit]
    facets: Dict[str, Dict[str, int]] = Field(default_factory=dict)


class DocumentClassifyResponse(BaseModel):
    request_id: str
    document_id: str
    classification: str
    confidence: float
    matched_rules: List[str]
    metadata_updated: bool


class DocumentAuditResponse(BaseModel):
    request_id: str
    document_id: str
    events: List[Dict[str, Any]]


class DocumentStatsResponse(BaseModel):
    status: str
    version: str
    total_documents: int
    active_documents: int
    archived_documents: int
    deleted_documents: int
    by_category: Dict[str, int]
    by_department: Dict[str, int]
    by_sensitivity: Dict[str, int]
    by_mime_type: Dict[str, int]


@dataclass
class DocumentRecord:
    document_id: str
    version: int
    filename: Optional[str]
    mime_type: str
    text: str
    metadata: DocumentMetadata
    status: DocumentStatus
    tenant_id: Optional[str]
    content_hash: str
    created_at: str
    updated_at: str
    versions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    tenant_id: Optional[str]
    started_at: float


class DocumentRepository:
    def __init__(self) -> None:
        self.documents: Dict[str, DocumentRecord] = {}
        self.audit: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)

    def upsert(self, record: DocumentRecord, actor: str, action: str) -> DocumentRecord:
        self.documents[record.document_id] = record
        self.add_audit(record.document_id, actor, action, {"version": record.version, "content_hash": record.content_hash})
        return record

    def get(self, document_id: str) -> Optional[DocumentRecord]:
        return self.documents.get(document_id)

    def delete(self, document_id: str, actor: str) -> bool:
        record = self.documents.get(document_id)
        if record is None:
            return False
        record.status = DocumentStatus.DELETED
        record.updated_at = utc_now_iso()
        self.add_audit(document_id, actor, "deleted", {})
        return True

    def list(self) -> List[DocumentRecord]:
        return list(self.documents.values())

    def add_audit(self, document_id: str, actor: str, action: str, details: Dict[str, Any]) -> None:
        self.audit[document_id].append(
            {
                "audit_id": f"aud_{uuid.uuid4().hex[:16]}",
                "document_id": document_id,
                "actor": actor,
                "action": action,
                "details": details,
                "created_at": utc_now_iso(),
            }
        )

    def audit_events(self, document_id: str) -> List[Dict[str, Any]]:
        return self.audit.get(document_id, [])


repository = DocumentRepository()


@router.get("/health")
async def documents_health() -> Dict[str, Any]:
    return {"status": "ok", "router": "documents", "version": ROUTER_VERSION, "timestamp": utc_now_iso()}


@router.post("/ingest", response_model=DocumentIngestResponse, dependencies=[Depends(require_scopes("documents:write"))])
async def ingest_document(payload: DocumentIngestRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> DocumentIngestResponse:
    ctx = build_context(request, user, payload.tenant_id)
    text, mime_type, warnings = extract_text_and_mime(payload)
    now = utc_now_iso()
    document_id = payload.document_id or f"doc_{uuid.uuid4().hex[:20]}"
    existing = repository.get(document_id)
    version = 1 if existing is None else existing.version + 1
    content_hash = hash_text(text)
    record = DocumentRecord(
        document_id=document_id,
        version=version,
        filename=payload.filename,
        mime_type=mime_type,
        text=text,
        metadata=payload.metadata,
        status=payload.status,
        tenant_id=ctx.tenant_id,
        content_hash=content_hash,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        versions=list(existing.versions if existing else []),
    )
    if existing:
        record.versions.append(version_snapshot(existing, "upsert"))
    repository.upsert(record, ctx.user_subject, "ingested" if existing is None else "updated")
    return DocumentIngestResponse(request_id=ctx.request_id, status="success", document=to_response(record), warnings=warnings)


@router.post("/search", response_model=DocumentSearchResponse, dependencies=[Depends(require_scopes("documents:read"))])
async def search_documents(payload: DocumentSearchRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> DocumentSearchResponse:
    ctx = build_context(request, user, None)
    rows = [record for record in repository.list() if visible_record(record, ctx.tenant_id, payload.include_archived)]
    rows = apply_filters(rows, payload.filters, payload.tags)
    scored = score_records(rows, payload.query, payload.mode)
    scored = [item for item in scored if item[1] > 0 or not payload.query]
    reverse = payload.sort_direction == SortDirection.DESC
    scored.sort(key=lambda item: sort_value(item[0], item[1], payload.sort_by), reverse=reverse)
    total = len(scored)
    page = scored[payload.offset : payload.offset + payload.limit]
    hits = [DocumentSearchHit(document=to_response(record), score=round(score, 4), highlights=highlights(record.text, payload.query)) for record, score in page]
    return DocumentSearchResponse(
        request_id=ctx.request_id,
        status="success",
        total=total,
        returned=len(hits),
        latency_ms=elapsed_ms(ctx.started_at),
        hits=hits,
        facets=facets(rows),
    )


@router.get("/{document_id}", response_model=DocumentRecordResponse, dependencies=[Depends(require_scopes("documents:read"))])
async def get_document(document_id: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> DocumentRecordResponse:
    ctx = build_context(request, user, None)
    record = repository.get(document_id)
    if record is None or record.status == DocumentStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado")
    if ctx.tenant_id and record.tenant_id and ctx.tenant_id != record.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant sem acesso ao documento")
    repository.add_audit(document_id, ctx.user_subject, "read", {})
    return to_response(record)


@router.delete("/{document_id}", dependencies=[Depends(require_scopes("documents:write"))])
async def delete_document(document_id: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    ctx = build_context(request, user, None)
    deleted = repository.delete(document_id, ctx.user_subject)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado")
    return {"request_id": ctx.request_id, "status": "deleted", "document_id": document_id}


@router.post("/{document_id}/classify", response_model=DocumentClassifyResponse, dependencies=[Depends(require_scopes("documents:read"))])
async def classify_document(document_id: str, payload: DocumentClassifyRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> DocumentClassifyResponse:
    ctx = build_context(request, user, None)
    record = repository.get(document_id)
    if record is None or record.status == DocumentStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado")
    classification, confidence, matches = classify_text(record.text, payload.rules or default_classification_rules())
    if payload.update_metadata:
        record.metadata.category = classification
        record.updated_at = utc_now_iso()
        repository.add_audit(document_id, ctx.user_subject, "classified", {"classification": classification, "confidence": confidence})
    return DocumentClassifyResponse(
        request_id=ctx.request_id,
        document_id=document_id,
        classification=classification,
        confidence=confidence,
        matched_rules=matches,
        metadata_updated=payload.update_metadata,
    )


@router.post("/{document_id}/versions", response_model=DocumentRecordResponse, dependencies=[Depends(require_scopes("documents:write"))])
async def add_document_version(document_id: str, payload: DocumentUpdateVersionRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> DocumentRecordResponse:
    ctx = build_context(request, user, None)
    existing = repository.get(document_id)
    if existing is None or existing.status == DocumentStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado")
    text = payload.text if payload.text is not None else existing.text
    if payload.content_base64:
        text = decode_content(payload.content_base64).decode("utf-8", errors="replace")
    metadata = existing.metadata.copy(deep=True)
    for key, value in payload.metadata_patch.items():
        if hasattr(metadata, key):
            setattr(metadata, key, value)
        else:
            metadata.custom[key] = value
    new_record = DocumentRecord(
        document_id=document_id,
        version=existing.version + 1,
        filename=existing.filename,
        mime_type=existing.mime_type,
        text=text,
        metadata=metadata,
        status=existing.status,
        tenant_id=existing.tenant_id,
        content_hash=hash_text(text),
        created_at=existing.created_at,
        updated_at=utc_now_iso(),
        versions=existing.versions + [version_snapshot(existing, payload.change_reason)],
    )
    repository.upsert(new_record, ctx.user_subject, "version_added")
    return to_response(new_record)


@router.get("/{document_id}/audit", response_model=DocumentAuditResponse, dependencies=[Depends(require_scopes("documents:read"))])
async def document_audit(document_id: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> DocumentAuditResponse:
    ctx = build_context(request, user, None)
    if repository.get(document_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado")
    return DocumentAuditResponse(request_id=ctx.request_id, document_id=document_id, events=repository.audit_events(document_id))


@router.get("/stats/summary", response_model=DocumentStatsResponse, dependencies=[Depends(require_scopes("documents:read"))])
async def document_stats() -> DocumentStatsResponse:
    records = repository.list()
    return DocumentStatsResponse(
        status="success",
        version=ROUTER_VERSION,
        total_documents=len(records),
        active_documents=sum(1 for item in records if item.status == DocumentStatus.ACTIVE),
        archived_documents=sum(1 for item in records if item.status == DocumentStatus.ARCHIVED),
        deleted_documents=sum(1 for item in records if item.status == DocumentStatus.DELETED),
        by_category=dict(Counter(item.metadata.category or "unknown" for item in records)),
        by_department=dict(Counter(item.metadata.department or "unknown" for item in records)),
        by_sensitivity=dict(Counter(item.metadata.sensitivity.value for item in records)),
        by_mime_type=dict(Counter(item.mime_type for item in records)),
    )


def build_context(request: Request, user: Any, tenant_override: Optional[str]) -> ExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    tenant = tenant_override or getattr(user, "tenant_id", None) or (user.get("tenant_id") if isinstance(user, dict) else None)
    return ExecutionContext(request_id=request_id, user_subject=str(subject), tenant_id=tenant, started_at=time.perf_counter())


def extract_text_and_mime(payload: DocumentIngestRequest) -> Tuple[str, str, List[str]]:
    warnings: List[str] = []
    mime_type = payload.mime_type or guess_mime_type(payload.filename)
    if payload.text is not None:
        text = payload.text
    elif payload.content_base64:
        raw = decode_content(payload.content_base64)
        if len(raw) > MAX_CONTENT_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Conteúdo excede limite")
        text = raw.decode("utf-8", errors="replace")
        warnings.append("content_base64_decoded_as_utf8")
    else:
        text = ""
        warnings.append("empty_document_text")
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Texto excede limite")
    return text, mime_type, warnings


def decode_content(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="content_base64 inválido") from exc


def guess_mime_type(filename: Optional[str]) -> str:
    if not filename:
        return "text/plain"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def visible_record(record: DocumentRecord, tenant_id: Optional[str], include_archived: bool) -> bool:
    if record.status == DocumentStatus.DELETED:
        return False
    if record.status == DocumentStatus.ARCHIVED and not include_archived:
        return False
    if tenant_id and record.tenant_id and record.tenant_id != tenant_id:
        return False
    return True


def apply_filters(records: Sequence[DocumentRecord], filters: Mapping[str, Any], tags: Sequence[str]) -> List[DocumentRecord]:
    result = list(records)
    if tags:
        wanted = {tag.lower() for tag in tags}
        result = [record for record in result if wanted.issubset({tag.lower() for tag in record.metadata.tags})]
    for key, value in filters.items():
        result = [record for record in result if str(get_record_field(record, key)).lower() == str(value).lower()]
    return result


def get_record_field(record: DocumentRecord, field: str) -> Any:
    mapping = {
        "document_id": record.document_id,
        "filename": record.filename,
        "mime_type": record.mime_type,
        "status": record.status.value,
        "tenant_id": record.tenant_id,
        "title": record.metadata.title,
        "source": record.metadata.source,
        "owner": record.metadata.owner,
        "department": record.metadata.department,
        "category": record.metadata.category,
        "language": record.metadata.language,
        "sensitivity": record.metadata.sensitivity.value,
    }
    if field in mapping:
        return mapping[field]
    if field.startswith("custom."):
        return record.metadata.custom.get(field.split(".", 1)[1])
    return None


def score_records(records: Sequence[DocumentRecord], query: str, mode: SearchMode) -> List[Tuple[DocumentRecord, float]]:
    if not query.strip():
        return [(record, 1.0) for record in records]
    q = query.lower().strip()
    terms = tokenize(q)
    scored: List[Tuple[DocumentRecord, float]] = []
    for record in records:
        haystack = " ".join([record.text, record.metadata.title or "", record.metadata.category or "", " ".join(record.metadata.tags)]).lower()
        if mode == SearchMode.EXACT:
            score = 10.0 if q in haystack else 0.0
        elif mode == SearchMode.PREFIX:
            tokens = tokenize(haystack)
            score = float(sum(1 for term in terms for token in tokens if token.startswith(term)))
        else:
            tokens = tokenize(haystack)
            counts = Counter(tokens)
            score = float(sum(counts.get(term, 0) for term in terms))
            if q in haystack:
                score += 5.0
        scored.append((record, score))
    return scored


def highlights(text: str, query: str, max_items: int = 3) -> List[str]:
    if not query.strip() or not text:
        return []
    q = re.escape(query.strip())
    result: List[str] = []
    for match in re.finditer(q, text, flags=re.IGNORECASE):
        start = max(match.start() - 60, 0)
        end = min(match.end() + 60, len(text))
        result.append(text[start:end].replace("\n", " "))
        if len(result) >= max_items:
            break
    if result:
        return result
    terms = tokenize(query)
    for term in terms:
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            start = max(match.start() - 60, 0)
            end = min(match.end() + 60, len(text))
            result.append(text[start:end].replace("\n", " "))
            break
        if len(result) >= max_items:
            break
    return result


def classify_text(text: str, rules: Mapping[str, Sequence[str]]) -> Tuple[str, float, List[str]]:
    normalized = text.lower()
    scores: Dict[str, List[str]] = {}
    for category, keywords in rules.items():
        matches = [keyword for keyword in keywords if keyword.lower() in normalized]
        if matches:
            scores[category] = matches
    if not scores:
        return "general", 0.35, []
    category, matches = max(scores.items(), key=lambda item: len(item[1]))
    confidence = min(0.45 + len(matches) * 0.12, 0.95)
    return category, round(confidence, 4), list(matches)


def default_classification_rules() -> Dict[str, List[str]]:
    return {
        "financial": ["invoice", "fatura", "pagamento", "receita", "despesa", "cashflow", "budget"],
        "legal": ["contrato", "contract", "cláusula", "clausula", "jurídico", "legal", "assinatura"],
        "hr": ["folha", "payroll", "colaborador", "employee", "benefício", "beneficio", "salário", "salario"],
        "risk": ["risco", "fraude", "fraud", "compliance", "auditoria", "audit", "anomalia"],
        "technical": ["api", "endpoint", "database", "erro", "stacktrace", "deploy", "latência", "latencia"],
    }


def facets(records: Sequence[DocumentRecord]) -> Dict[str, Dict[str, int]]:
    return {
        "category": dict(Counter(record.metadata.category or "unknown" for record in records)),
        "department": dict(Counter(record.metadata.department or "unknown" for record in records)),
        "sensitivity": dict(Counter(record.metadata.sensitivity.value for record in records)),
        "mime_type": dict(Counter(record.mime_type for record in records)),
        "status": dict(Counter(record.status.value for record in records)),
    }


def sort_value(record: DocumentRecord, score: float, sort_by: str) -> Any:
    if sort_by == "score":
        return score
    if sort_by == "created_at":
        return record.created_at
    if sort_by == "updated_at":
        return record.updated_at
    if sort_by == "title":
        return record.metadata.title or ""
    if sort_by == "text_length":
        return len(record.text)
    return record.updated_at


def version_snapshot(record: DocumentRecord, reason: str) -> Dict[str, Any]:
    return {
        "version": record.version,
        "content_hash": record.content_hash,
        "text_length": len(record.text),
        "updated_at": record.updated_at,
        "reason": reason,
    }


def to_response(record: DocumentRecord) -> DocumentRecordResponse:
    return DocumentRecordResponse(
        document_id=record.document_id,
        version=record.version,
        filename=record.filename,
        mime_type=record.mime_type,
        title=record.metadata.title,
        status=record.status,
        sensitivity=record.metadata.sensitivity,
        tenant_id=record.tenant_id,
        owner=record.metadata.owner,
        department=record.metadata.department,
        category=record.metadata.category,
        language=record.metadata.language,
        tags=record.metadata.tags,
        text_preview=preview_text(record.text),
        text_length=len(record.text),
        content_hash=record.content_hash,
        created_at=record.created_at,
        updated_at=record.updated_at,
        metadata=record.metadata.custom,
    )


def preview_text(text: str, length: int = 500) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:length]


def tokenize(value: str) -> List[str]:
    return re.findall(r"[\wÀ-ÿ]+", value.lower())


def hash_text(text: str, length: int = 32) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
