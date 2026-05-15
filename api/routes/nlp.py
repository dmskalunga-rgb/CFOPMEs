#!/usr/bin/env python3
"""
api/routes/nlp.py

Enterprise-grade NLP API Router.

Objetivo:
- Expor endpoints HTTP para processamento de linguagem natural sem dependências pesadas obrigatórias.
- Fornecer análise de texto, normalização, tokenização, entidades simples, palavras-chave,
  sentimento heurístico, classificação por regras, resumo extrativo, similaridade e batch processing.
- Aplicar padrões enterprise: validação Pydantic, auth por scopes, request-id, respostas padronizadas,
  limites de payload, auditoria leve e proteção contra vazamento de dados sensíveis.

Endpoints:
    GET  /nlp/health
    POST /nlp/analyze
    POST /nlp/batch-analyze
    POST /nlp/keywords
    POST /nlp/entities
    POST /nlp/sentiment
    POST /nlp/classify
    POST /nlp/summarize
    POST /nlp/similarity
    POST /nlp/normalize

Integração:
    from fastapi import FastAPI
    from api.routes.nlp import router as nlp_router

    app.include_router(nlp_router, prefix="/v1")

Notas:
- Este módulo é dependency-light e funciona sem spaCy/NLTK/transformers.
- Para produção com modelos avançados, substitua NlpEngine por adapter de modelo externo.
"""

from __future__ import annotations

import hashlib
import html
import logging
import math
import re
import statistics
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
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
MAX_TEXT_LENGTH = 200_000
MAX_BATCH_SIZE = 1_000

router = APIRouter(prefix="/nlp", tags=["nlp"])


class Language(str, Enum):
    AUTO = "auto"
    PT = "pt"
    EN = "en"
    ES = "es"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


class EntityType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    MONEY = "money"
    DATE = "date"
    DOCUMENT_ID = "document_id"
    HASHTAG = "hashtag"
    MENTION = "mention"
    CAPITALIZED_PHRASE = "capitalized_phrase"


class SummaryMode(str, Enum):
    EXTRACTIVE = "extractive"
    BULLETS = "bullets"


class SimilarityMethod(str, Enum):
    JACCARD = "jaccard"
    COSINE_TF = "cosine_tf"


class TextRequest(BaseModel):
    text: str = Field(..., min_length=1)
    language: Language = Language.AUTO
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("text")
    def validate_text_length(cls, value: str) -> str:
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"text excede limite de {MAX_TEXT_LENGTH} caracteres")
        return value


class BatchTextRequest(BaseModel):
    items: List[TextRequest] = Field(default_factory=list)
    include_keywords: bool = True
    include_entities: bool = True
    include_sentiment: bool = True
    include_summary: bool = False

    @validator("items")
    def validate_batch_size(cls, value: List[TextRequest]) -> List[TextRequest]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"batch excede limite de {MAX_BATCH_SIZE}")
        return value


class KeywordRequest(TextRequest):
    top_k: int = Field(default=20, ge=1, le=200)
    min_token_length: int = Field(default=3, ge=1, le=50)
    include_ngrams: bool = True


class ClassifyRequest(TextRequest):
    labels: Dict[str, List[str]] = Field(default_factory=dict)
    threshold: float = Field(default=0.1, ge=0, le=1)
    allow_multi_label: bool = False


class SummarizeRequest(TextRequest):
    max_sentences: int = Field(default=3, ge=1, le=20)
    mode: SummaryMode = SummaryMode.EXTRACTIVE


class SimilarityRequest(BaseModel):
    left_text: str = Field(..., min_length=1)
    right_text: str = Field(..., min_length=1)
    language: Language = Language.AUTO
    method: SimilarityMethod = SimilarityMethod.COSINE_TF

    @validator("left_text", "right_text")
    def validate_similarity_text_length(cls, value: str) -> str:
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"texto excede limite de {MAX_TEXT_LENGTH} caracteres")
        return value


class KeywordItem(BaseModel):
    keyword: str
    score: float
    count: int


class EntityItem(BaseModel):
    type: EntityType
    value: str
    normalized: str
    start: int
    end: int
    confidence: float


class SentimentResult(BaseModel):
    label: SentimentLabel
    score: float
    positive_score: float
    negative_score: float
    reasons: List[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    label: str
    score: float
    matched_terms: List[str] = Field(default_factory=list)


class TextStats(BaseModel):
    char_count: int
    word_count: int
    sentence_count: int
    unique_token_count: int
    avg_sentence_length: float
    language_detected: str
    text_hash: str


class NlpAnalyzeResult(BaseModel):
    stats: TextStats
    normalized_text_preview: str
    keywords: List[KeywordItem] = Field(default_factory=list)
    entities: List[EntityItem] = Field(default_factory=list)
    sentiment: Optional[SentimentResult] = None
    summary: Optional[str] = None


class NlpResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    result: Dict[str, Any]
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BatchNlpResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    total_items: int
    results: List[NlpAnalyzeResult]
    warnings: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


STOPWORDS: Dict[str, Set[str]] = {
    "pt": {
        "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
        "por", "para", "com", "sem", "que", "e", "ou", "mas", "se", "sua", "seu", "suas", "seus", "ao", "aos", "à", "às",
        "é", "são", "foi", "ser", "ter", "tem", "mais", "menos", "muito", "muita", "muitos", "muitas", "este", "esta", "esse", "essa",
        "isso", "isto", "como", "quando", "onde", "porque", "também", "tambem", "já", "ja", "não", "nao", "sim",
    },
    "en": {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "to", "in", "on", "for", "with", "without", "is", "are",
        "was", "were", "be", "been", "being", "have", "has", "had", "this", "that", "these", "those", "it", "its", "as", "at", "by",
        "from", "not", "yes", "no", "more", "less", "very", "can", "could", "should", "would",
    },
    "es": {
        "el", "la", "los", "las", "un", "una", "de", "del", "en", "con", "sin", "para", "por", "que", "y", "o", "pero", "si",
        "es", "son", "fue", "ser", "tener", "tiene", "más", "mas", "menos", "muy", "este", "esta", "ese", "esa", "no", "sí", "si",
    },
}

POSITIVE_WORDS = {
    "bom", "boa", "ótimo", "otimo", "excelente", "incrível", "incrivel", "positivo", "sucesso", "crescimento", "feliz", "satisfeito",
    "good", "great", "excellent", "amazing", "positive", "success", "growth", "happy", "satisfied", "love", "best",
    "bueno", "excelente", "positivo", "éxito", "exito", "feliz", "satisfecho",
}

NEGATIVE_WORDS = {
    "ruim", "péssimo", "pessimo", "horrível", "horrivel", "negativo", "erro", "falha", "problema", "perda", "atraso", "insatisfeito",
    "bad", "terrible", "awful", "negative", "error", "failure", "problem", "loss", "delay", "angry", "worst",
    "malo", "terrible", "negativo", "error", "fallo", "problema", "pérdida", "perdida", "retraso",
}

DEFAULT_LABEL_RULES: Dict[str, List[str]] = {
    "finance": ["receita", "despesa", "fatura", "pagamento", "cashflow", "fluxo de caixa", "budget", "revenue", "cost", "invoice"],
    "fraud": ["fraude", "suspeito", "chargeback", "risco", "anomalia", "fraud", "suspicious", "risk", "anomaly"],
    "support": ["ajuda", "suporte", "erro", "problema", "ticket", "help", "support", "issue", "bug"],
    "sales": ["venda", "cliente", "proposta", "contrato", "lead", "sales", "customer", "deal", "proposal"],
    "hr": ["colaborador", "folha", "salário", "benefício", "employee", "payroll", "salary", "benefit"],
}


class NlpEngine:
    def analyze(
        self,
        text: str,
        language: Language = Language.AUTO,
        include_keywords: bool = True,
        include_entities: bool = True,
        include_sentiment: bool = True,
        include_summary: bool = False,
    ) -> NlpAnalyzeResult:
        detected = detect_language(text) if language == Language.AUTO else language.value
        normalized = normalize_text(text)
        tokens = tokenize(normalized)
        sentences = split_sentences(text)
        stats = TextStats(
            char_count=len(text),
            word_count=len(tokens),
            sentence_count=len(sentences),
            unique_token_count=len(set(tokens)),
            avg_sentence_length=round(len(tokens) / max(len(sentences), 1), 4),
            language_detected=detected,
            text_hash=hash_text(text),
        )
        return NlpAnalyzeResult(
            stats=stats,
            normalized_text_preview=normalized[:500],
            keywords=self.keywords(text, Language(detected) if detected in Language._value2member_map_ else Language.AUTO, 20, 3, True) if include_keywords else [],
            entities=self.entities(text) if include_entities else [],
            sentiment=self.sentiment(text, detected) if include_sentiment else None,
            summary=self.summarize(text, 3, SummaryMode.EXTRACTIVE) if include_summary else None,
        )

    def keywords(self, text: str, language: Language, top_k: int, min_token_length: int, include_ngrams: bool) -> List[KeywordItem]:
        detected = detect_language(text) if language == Language.AUTO else language.value
        normalized = normalize_text(text)
        tokens = [token for token in tokenize(normalized) if len(token) >= min_token_length and token not in STOPWORDS.get(detected, set())]
        counts = Counter(tokens)
        scores: Counter[str] = Counter()
        for token, count in counts.items():
            scores[token] = count * (1 + math.log(max(len(token), 1)))
        if include_ngrams:
            for n in (2, 3):
                for gram in ngrams(tokens, n):
                    phrase = " ".join(gram)
                    scores[phrase] += DecimalLike(len(phrase.split()))
        items = []
        for keyword, score in scores.most_common(top_k):
            count = counts.get(keyword, 1 if " " in keyword else 0)
            items.append(KeywordItem(keyword=keyword, score=round(float(score), 4), count=int(count)))
        return items

    def entities(self, text: str) -> List[EntityItem]:
        patterns: List[Tuple[EntityType, str, float]] = [
            (EntityType.EMAIL, r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", 0.98),
            (EntityType.URL, r"\bhttps?://[^\s]+|\bwww\.[^\s]+", 0.95),
            (EntityType.PHONE, r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,3}\)?[\s.-]?)?\d{4,5}[\s.-]?\d{4}(?!\d)", 0.82),
            (EntityType.MONEY, r"(?:R\$|US\$|USD|BRL|€|\$)\s?\d{1,3}(?:[\.\s]?\d{3})*(?:,\d{2}|\.\d{2})?", 0.9),
            (EntityType.DATE, r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", 0.86),
            (EntityType.DOCUMENT_ID, r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", 0.78),
            (EntityType.HASHTAG, r"#[\wÀ-ÿ_]+", 0.9),
            (EntityType.MENTION, r"@[\wÀ-ÿ_]+", 0.9),
            (EntityType.CAPITALIZED_PHRASE, r"\b[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){1,3}\b", 0.55),
        ]
        entities: List[EntityItem] = []
        seen: Set[Tuple[int, int, str]] = set()
        for entity_type, pattern, confidence in patterns:
            for match in re.finditer(pattern, text):
                key = (match.start(), match.end(), entity_type.value)
                if key in seen:
                    continue
                seen.add(key)
                value = match.group(0)
                entities.append(
                    EntityItem(
                        type=entity_type,
                        value=redact_sensitive_entity(value, entity_type),
                        normalized=normalize_entity(value, entity_type),
                        start=match.start(),
                        end=match.end(),
                        confidence=confidence,
                    )
                )
        return sorted(entities, key=lambda item: (item.start, item.end))

    def sentiment(self, text: str, language: str) -> SentimentResult:
        tokens = tokenize(normalize_text(text))
        positive = sum(1 for token in tokens if token in POSITIVE_WORDS)
        negative = sum(1 for token in tokens if token in NEGATIVE_WORDS)
        total = max(positive + negative, 1)
        positive_score = positive / total
        negative_score = negative / total
        raw = positive - negative
        if positive > 0 and negative > 0 and abs(raw) <= 1:
            label = SentimentLabel.MIXED
        elif raw > 0:
            label = SentimentLabel.POSITIVE
        elif raw < 0:
            label = SentimentLabel.NEGATIVE
        else:
            label = SentimentLabel.NEUTRAL
        score = max(positive_score, negative_score) if label != SentimentLabel.NEUTRAL else 0.0
        reasons: List[str] = []
        if positive:
            reasons.append(f"positive_terms={positive}")
        if negative:
            reasons.append(f"negative_terms={negative}")
        return SentimentResult(
            label=label,
            score=round(float(score), 4),
            positive_score=round(float(positive_score), 4),
            negative_score=round(float(negative_score), 4),
            reasons=reasons or ["no_sentiment_terms_detected"],
        )

    def classify(self, text: str, labels: Mapping[str, Sequence[str]], threshold: float, allow_multi_label: bool) -> List[ClassificationResult]:
        rules = labels or DEFAULT_LABEL_RULES
        normalized = normalize_text(text)
        results: List[ClassificationResult] = []
        for label, terms in rules.items():
            matched = [term for term in terms if normalize_text(term) in normalized]
            score = len(matched) / max(len(terms), 1)
            if score >= threshold and matched:
                results.append(ClassificationResult(label=label, score=round(score, 4), matched_terms=matched))
        results.sort(key=lambda item: item.score, reverse=True)
        if not allow_multi_label and results:
            return [results[0]]
        return results or [ClassificationResult(label="unclassified", score=0.0, matched_terms=[])]

    def summarize(self, text: str, max_sentences: int, mode: SummaryMode) -> str:
        sentences = split_sentences(text)
        if len(sentences) <= max_sentences:
            selected = sentences
        else:
            normalized = normalize_text(text)
            tokens = [token for token in tokenize(normalized) if token not in STOPWORDS.get(detect_language(text), set())]
            freq = Counter(tokens)
            scored: List[Tuple[int, float, str]] = []
            for index, sentence in enumerate(sentences):
                sentence_tokens = tokenize(normalize_text(sentence))
                score = sum(freq.get(token, 0) for token in sentence_tokens) / max(len(sentence_tokens), 1)
                if index == 0:
                    score *= 1.15
                scored.append((index, score, sentence))
            top = sorted(scored, key=lambda item: item[1], reverse=True)[:max_sentences]
            selected = [sentence for _, _, sentence in sorted(top, key=lambda item: item[0])]
        if mode == SummaryMode.BULLETS:
            return "\n".join(f"- {sentence.strip()}" for sentence in selected)
        return " ".join(sentence.strip() for sentence in selected)

    def similarity(self, left: str, right: str, method: SimilarityMethod, language: Language) -> Dict[str, Any]:
        detected = detect_language(left + " " + right) if language == Language.AUTO else language.value
        left_tokens = [token for token in tokenize(normalize_text(left)) if token not in STOPWORDS.get(detected, set())]
        right_tokens = [token for token in tokenize(normalize_text(right)) if token not in STOPWORDS.get(detected, set())]
        if method == SimilarityMethod.JACCARD:
            score = jaccard(left_tokens, right_tokens)
        else:
            score = cosine_tf(left_tokens, right_tokens)
        return {
            "method": method.value,
            "language_detected": detected,
            "score": round(score, 6),
            "left_token_count": len(left_tokens),
            "right_token_count": len(right_tokens),
            "shared_terms": sorted(list(set(left_tokens) & set(right_tokens)))[:50],
        }


engine = NlpEngine()


@router.get("/health")
async def nlp_health() -> Dict[str, Any]:
    return {"status": "ok", "router": "nlp", "version": ROUTER_VERSION, "timestamp": utc_now_iso()}


@router.post("/analyze", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def analyze_text(payload: TextRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    result = engine.analyze(payload.text, payload.language, True, True, True, True)
    return response(ctx, result.dict(), metadata={"operation": "analyze"})


@router.post("/batch-analyze", response_model=BatchNlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def batch_analyze(payload: BatchTextRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> BatchNlpResponse:
    ctx = build_context(request, user)
    results = [
        engine.analyze(item.text, item.language, payload.include_keywords, payload.include_entities, payload.include_sentiment, payload.include_summary)
        for item in payload.items
    ]
    return BatchNlpResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        total_items=len(results),
        results=results,
        warnings=["empty_batch"] if not payload.items else [],
    )


@router.post("/keywords", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def extract_keywords(payload: KeywordRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    keywords = engine.keywords(payload.text, payload.language, payload.top_k, payload.min_token_length, payload.include_ngrams)
    return response(ctx, {"keywords": [item.dict() for item in keywords]}, metadata={"operation": "keywords"})


@router.post("/entities", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def extract_entities(payload: TextRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    entities = engine.entities(payload.text)
    return response(ctx, {"entities": [item.dict() for item in entities]}, metadata={"operation": "entities"})


@router.post("/sentiment", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def analyze_sentiment(payload: TextRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    detected = detect_language(payload.text) if payload.language == Language.AUTO else payload.language.value
    sentiment = engine.sentiment(payload.text, detected)
    return response(ctx, {"sentiment": sentiment.dict(), "language_detected": detected}, metadata={"operation": "sentiment"})


@router.post("/classify", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def classify_text_endpoint(payload: ClassifyRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    classifications = engine.classify(payload.text, payload.labels, payload.threshold, payload.allow_multi_label)
    return response(ctx, {"classifications": [item.dict() for item in classifications]}, metadata={"operation": "classify"})


@router.post("/summarize", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def summarize_text(payload: SummarizeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    summary = engine.summarize(payload.text, payload.max_sentences, payload.mode)
    return response(ctx, {"summary": summary, "mode": payload.mode.value}, metadata={"operation": "summarize"})


@router.post("/similarity", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def text_similarity(payload: SimilarityRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    result = engine.similarity(payload.left_text, payload.right_text, payload.method, payload.language)
    return response(ctx, result, metadata={"operation": "similarity"})


@router.post("/normalize", response_model=NlpResponse, dependencies=[Depends(require_scopes("nlp:read"))])
async def normalize_text_endpoint(payload: TextRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> NlpResponse:
    ctx = build_context(request, user)
    normalized = normalize_text(payload.text)
    return response(
        ctx,
        {
            "normalized_text": normalized,
            "text_hash": hash_text(payload.text),
            "normalized_hash": hash_text(normalized),
            "language_detected": detect_language(payload.text) if payload.language == Language.AUTO else payload.language.value,
        },
        metadata={"operation": "normalize"},
    )


def response(ctx: ExecutionContext, result: Dict[str, Any], warnings: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> NlpResponse:
    return NlpResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        result=result,
        warnings=warnings or [],
        metadata={"user": ctx.user_subject, **(metadata or {})},
    )


def build_context(request: Request, user: Any) -> ExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    return ExecutionContext(request_id=request_id, user_subject=str(subject), started_at=time.perf_counter())


def normalize_text(text: str) -> str:
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def tokenize(text: str) -> List[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.lower())


def split_sentences(text: str) -> List[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    sentences = re.split(r"(?<=[.!?。！？])\s+", cleaned)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def ngrams(tokens: Sequence[str], n: int) -> Iterable[Tuple[str, ...]]:
    for index in range(0, max(len(tokens) - n + 1, 0)):
        yield tuple(tokens[index : index + n])


def detect_language(text: str) -> str:
    normalized = normalize_text(text)
    tokens = tokenize(normalized)
    if not tokens:
        return "pt"
    scores = {lang: sum(1 for token in tokens if token in words) for lang, words in STOPWORDS.items()}
    if "não" in normalized or "ção" in normalized or "ões" in normalized:
        scores["pt"] += 3
    if " the " in f" {normalized} " or " and " in f" {normalized} ":
        scores["en"] += 3
    if " el " in f" {normalized} " or " una " in f" {normalized} ":
        scores["es"] += 2
    return max(scores.items(), key=lambda item: item[1])[0] if max(scores.values()) > 0 else "pt"


def hash_text(text: str, length: int = 32) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def redact_sensitive_entity(value: str, entity_type: EntityType) -> str:
    if entity_type == EntityType.EMAIL:
        local, _, domain = value.partition("@")
        return f"{local[:2]}***@{domain}"
    if entity_type in {EntityType.PHONE, EntityType.DOCUMENT_ID}:
        digits = re.sub(r"\D", "", value)
        return f"***{digits[-4:]}" if len(digits) >= 4 else "***"
    return value


def normalize_entity(value: str, entity_type: EntityType) -> str:
    if entity_type in {EntityType.PHONE, EntityType.DOCUMENT_ID}:
        return re.sub(r"\D", "", value)
    if entity_type == EntityType.EMAIL:
        return value.lower()
    if entity_type == EntityType.URL:
        return value.rstrip(".,;)").lower()
    return normalize_text(value)


def jaccard(left_tokens: Sequence[str], right_tokens: Sequence[str]) -> float:
    left = set(left_tokens)
    right = set(right_tokens)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def cosine_tf(left_tokens: Sequence[str], right_tokens: Sequence[str]) -> float:
    left = Counter(left_tokens)
    right = Counter(right_tokens)
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    dot = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


def DecimalLike(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
