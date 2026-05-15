/**
 * edge_functions/cache_layer.ts
 *
 * Enterprise-grade Supabase Edge Function for a platform cache layer.
 *
 * Runtime: Deno / Supabase Edge Functions
 *
 * Capabilities:
 * - Namespaced key/value cache API
 * - TTL and stale-while-revalidate metadata
 * - ETag/hash support
 * - Conditional GET with If-None-Match
 * - Cache get/set/delete/invalidate endpoints
 * - Pattern-based namespace invalidation
 * - Optional compression-safe JSON payload storage
 * - API key/Bearer pass-through guard
 * - CORS handling
 * - Structured responses and request correlation IDs
 * - Audit events
 * - Metrics counters stored through RPC/table hooks when available
 * - Defensive payload limits
 *
 * Suggested table:
 *
 * create table if not exists edge_cache_entries (
 *   namespace text not null,
 *   cache_key text not null,
 *   value jsonb not null,
 *   etag text not null,
 *   tags text[] default '{}',
 *   metadata jsonb default '{}',
 *   expires_at timestamptz,
 *   stale_until timestamptz,
 *   created_at timestamptz not null default now(),
 *   updated_at timestamptz not null default now(),
 *   primary key(namespace, cache_key)
 * );
 *
 * create index if not exists idx_edge_cache_entries_expires_at on edge_cache_entries(expires_at);
 * create index if not exists idx_edge_cache_entries_tags on edge_cache_entries using gin(tags);
 */

import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// =============================================================================
// Types
// =============================================================================

type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
type JsonObject = { [key: string]: JsonValue };
type CacheOperation = "get" | "set" | "delete" | "invalidate" | "purge_expired" | "health";
type CacheStatus = "hit" | "miss" | "stale" | "set" | "deleted" | "invalidated" | "error";

interface CacheConfig {
  supabaseUrl: string;
  supabaseServiceRoleKey: string;
  allowedOrigins: string[];
  allowCredentials: boolean;
  defaultNamespace: string;
  defaultTtlSeconds: number;
  defaultStaleSeconds: number;
  maxTtlSeconds: number;
  maxPayloadBytes: number;
  tableName: string;
  auditTableName: string;
  enableAudit: boolean;
  enableMetrics: boolean;
  requireAuth: boolean;
  apiKey: string;
  correlationHeader: string;
  environment: string;
  serviceName: string;
}

interface CacheRequestBody {
  operation?: CacheOperation;
  namespace?: string;
  key?: string;
  value?: JsonValue;
  ttlSeconds?: number;
  staleSeconds?: number;
  tags?: string[];
  metadata?: Record<string, unknown>;
  pattern?: string;
  deleteByTags?: string[];
}

interface CacheEntry {
  namespace: string;
  cache_key: string;
  value: JsonValue;
  etag: string;
  tags: string[] | null;
  metadata: Record<string, unknown> | null;
  expires_at: string | null;
  stale_until: string | null;
  created_at?: string;
  updated_at?: string;
}

interface CacheResponsePayload {
  ok: boolean;
  operation: CacheOperation;
  status: CacheStatus;
  namespace: string;
  key?: string | null;
  value?: JsonValue;
  etag?: string | null;
  fresh?: boolean;
  stale?: boolean;
  expired?: boolean;
  affectedRows?: number;
  metadata?: Record<string, unknown>;
  correlationId: string;
  timestamp: string;
}

interface AuditEvent {
  event_type: string;
  operation: CacheOperation;
  status: CacheStatus;
  namespace: string;
  cache_key?: string | null;
  correlation_id: string;
  ip_address?: string | null;
  user_agent?: string | null;
  metadata: Record<string, unknown>;
}

// =============================================================================
// Config
// =============================================================================

function getEnv(name: string, fallback = ""): string {
  return Deno.env.get(name) ?? fallback;
}

function getBooleanEnv(name: string, fallback: boolean): boolean {
  const raw = Deno.env.get(name);
  if (raw == null || raw === "") return fallback;
  return ["1", "true", "yes", "y", "on"].includes(raw.toLowerCase());
}

function getNumberEnv(name: string, fallback: number): number {
  const raw = Deno.env.get(name);
  if (raw == null || raw === "") return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function getListEnv(name: string, fallback: string[] = []): string[] {
  const raw = Deno.env.get(name);
  if (!raw) return fallback;
  return raw.split(",").map((x) => x.trim()).filter(Boolean);
}

function loadConfig(): CacheConfig {
  const config: CacheConfig = {
    supabaseUrl: getEnv("SUPABASE_URL"),
    supabaseServiceRoleKey: getEnv("SUPABASE_SERVICE_ROLE_KEY"),
    allowedOrigins: getListEnv("CACHE_ALLOWED_ORIGINS", ["*"]),
    allowCredentials: getBooleanEnv("CACHE_ALLOW_CREDENTIALS", true),
    defaultNamespace: getEnv("CACHE_DEFAULT_NAMESPACE", "default"),
    defaultTtlSeconds: getNumberEnv("CACHE_DEFAULT_TTL_SECONDS", 300),
    defaultStaleSeconds: getNumberEnv("CACHE_DEFAULT_STALE_SECONDS", 300),
    maxTtlSeconds: getNumberEnv("CACHE_MAX_TTL_SECONDS", 86400),
    maxPayloadBytes: getNumberEnv("CACHE_MAX_PAYLOAD_BYTES", 1_000_000),
    tableName: getEnv("CACHE_TABLE_NAME", "edge_cache_entries"),
    auditTableName: getEnv("CACHE_AUDIT_TABLE_NAME", "edge_cache_audit_events"),
    enableAudit: getBooleanEnv("CACHE_ENABLE_AUDIT", true),
    enableMetrics: getBooleanEnv("CACHE_ENABLE_METRICS", true),
    requireAuth: getBooleanEnv("CACHE_REQUIRE_AUTH", false),
    apiKey: getEnv("CACHE_API_KEY", ""),
    correlationHeader: getEnv("CACHE_CORRELATION_HEADER", "x-correlation-id").toLowerCase(),
    environment: getEnv("APP_ENV", "development"),
    serviceName: getEnv("APP_SERVICE_NAME", "edge-cache-layer"),
  };

  if (!config.supabaseUrl) throw new Error("SUPABASE_URL is required");
  if (!config.supabaseServiceRoleKey) throw new Error("SUPABASE_SERVICE_ROLE_KEY is required");
  if (config.requireAuth && !config.apiKey) throw new Error("CACHE_API_KEY is required when CACHE_REQUIRE_AUTH=true");
  return config;
}

// =============================================================================
// Response / request helpers
// =============================================================================

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };
const ALLOWED_METHODS = "GET,POST,DELETE,OPTIONS";
const ALLOWED_HEADERS = "authorization,apikey,content-type,x-api-key,x-cache-key,x-cache-namespace,x-correlation-id,x-request-id,if-none-match";

function nowIso(): string {
  return new Date().toISOString();
}

function jsonResponse(payload: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: { ...JSON_HEADERS, ...headers },
  });
}

function emptyResponse(status = 204, headers: Record<string, string> = {}): Response {
  return new Response(null, { status, headers });
}

function corsHeaders(req: Request, config: CacheConfig): Record<string, string> {
  const origin = req.headers.get("origin") ?? "";
  const allowAll = config.allowedOrigins.includes("*");
  const allowedOrigin = allowAll ? "*" : config.allowedOrigins.includes(origin) ? origin : config.allowedOrigins[0] ?? "";
  const headers: Record<string, string> = {
    "access-control-allow-origin": allowedOrigin,
    "access-control-allow-methods": ALLOWED_METHODS,
    "access-control-allow-headers": ALLOWED_HEADERS,
    "access-control-max-age": "86400",
    "vary": "Origin",
  };
  if (config.allowCredentials && allowedOrigin !== "*") headers["access-control-allow-credentials"] = "true";
  return headers;
}

function getCorrelationId(req: Request, config: CacheConfig): string {
  return req.headers.get(config.correlationHeader) ?? req.headers.get("x-request-id") ?? crypto.randomUUID();
}

function getClientIp(req: Request): string | null {
  return req.headers.get("cf-connecting-ip") ?? req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? req.headers.get("x-real-ip") ?? null;
}

function errorResponse(message: string, status: number, operation: CacheOperation, namespace: string, correlationId: string, headers: Record<string, string>, code = "CACHE_ERROR"): Response {
  return jsonResponse({
    ok: false,
    operation,
    status: "error",
    namespace,
    error: { code, message },
    correlationId,
    timestamp: nowIso(),
  }, status, headers);
}

function cacheResponse(payload: CacheResponsePayload, status: number, headers: Record<string, string> = {}): Response {
  const responseHeaders: Record<string, string> = { ...headers };
  if (payload.etag) responseHeaders.etag = payload.etag;
  if (payload.status === "hit") responseHeaders["x-cache"] = "HIT";
  if (payload.status === "miss") responseHeaders["x-cache"] = "MISS";
  if (payload.status === "stale") responseHeaders["x-cache"] = "STALE";
  return jsonResponse(payload, status, responseHeaders);
}

function assertAuthorized(req: Request, config: CacheConfig): boolean {
  if (!config.requireAuth) return true;
  const supplied = req.headers.get("x-api-key") ?? req.headers.get("apikey") ?? "";
  return supplied.length > 0 && supplied === config.apiKey;
}

async function readBody(req: Request, config: CacheConfig): Promise<CacheRequestBody> {
  if (req.method === "GET" || req.method === "DELETE") {
    const url = new URL(req.url);
    return {
      operation: inferOperation(req),
      namespace: url.searchParams.get("namespace") ?? req.headers.get("x-cache-namespace") ?? undefined,
      key: url.searchParams.get("key") ?? req.headers.get("x-cache-key") ?? undefined,
      pattern: url.searchParams.get("pattern") ?? undefined,
      deleteByTags: splitCsv(url.searchParams.get("tags")),
    };
  }

  const text = await req.text();
  if (new TextEncoder().encode(text).byteLength > config.maxPayloadBytes) {
    throw new Error(`Payload exceeds max size of ${config.maxPayloadBytes} bytes`);
  }
  if (!text) return { operation: inferOperation(req) };
  try {
    const parsed = JSON.parse(text);
    return { operation: inferOperation(req), ...(parsed ?? {}) };
  } catch {
    throw new Error("Invalid JSON payload");
  }
}

function inferOperation(req: Request): CacheOperation {
  const url = new URL(req.url);
  const op = url.searchParams.get("operation") as CacheOperation | null;
  if (op) return op;
  if (url.pathname.endsWith("/health")) return "health";
  if (url.pathname.endsWith("/invalidate")) return "invalidate";
  if (url.pathname.endsWith("/purge-expired")) return "purge_expired";
  if (req.method === "GET") return "get";
  if (req.method === "DELETE") return "delete";
  return "set";
}

function splitCsv(value: string | null): string[] | undefined {
  if (!value) return undefined;
  const items = value.split(",").map((x) => x.trim()).filter(Boolean);
  return items.length ? items : undefined;
}

function normalizeNamespace(value: string | undefined, config: CacheConfig): string {
  return sanitizeKey(value || config.defaultNamespace, "namespace");
}

function normalizeKey(value: string | undefined): string {
  if (!value) throw new Error("Cache key is required");
  return sanitizeKey(value, "key");
}

function sanitizeKey(value: string, label: string): string {
  const clean = value.trim();
  if (!clean) throw new Error(`Cache ${label} cannot be empty`);
  if (clean.length > 512) throw new Error(`Cache ${label} is too long`);
  if (!/^[a-zA-Z0-9._:\-/]+$/.test(clean)) {
    throw new Error(`Cache ${label} contains invalid characters`);
  }
  return clean;
}

function clampTtl(ttlSeconds: number | undefined, config: CacheConfig): number {
  const ttl = ttlSeconds ?? config.defaultTtlSeconds;
  return Math.max(1, Math.min(config.maxTtlSeconds, Math.floor(ttl)));
}

function clampStale(staleSeconds: number | undefined, config: CacheConfig): number {
  const stale = staleSeconds ?? config.defaultStaleSeconds;
  return Math.max(0, Math.min(config.maxTtlSeconds, Math.floor(stale)));
}

function addSeconds(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

// =============================================================================
// Supabase / hashing
// =============================================================================

function serviceClient(config: CacheConfig): SupabaseClient {
  return createClient(config.supabaseUrl, config.supabaseServiceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { headers: { "x-application-name": config.serviceName } },
  });
}

async function sha256Hex(value: unknown): Promise<string> {
  const serialized = typeof value === "string" ? value : JSON.stringify(value);
  const encoded = new TextEncoder().encode(serialized);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function weakEtag(hash: string): string {
  return `W/"${hash}"`;
}

function isExpired(entry: CacheEntry): boolean {
  if (!entry.expires_at) return false;
  return new Date(entry.expires_at).getTime() <= Date.now();
}

function isStaleAvailable(entry: CacheEntry): boolean {
  if (!entry.stale_until) return false;
  return new Date(entry.stale_until).getTime() > Date.now();
}

// =============================================================================
// Operations
// =============================================================================

async function getEntry(admin: SupabaseClient, config: CacheConfig, namespace: string, key: string): Promise<CacheEntry | null> {
  const { data, error } = await admin
    .from(config.tableName)
    .select("namespace,cache_key,value,etag,tags,metadata,expires_at,stale_until,created_at,updated_at")
    .eq("namespace", namespace)
    .eq("cache_key", key)
    .maybeSingle<CacheEntry>();
  if (error) throw new Error(`Cache get failed: ${error.message}`);
  return data ?? null;
}

async function setEntry(admin: SupabaseClient, config: CacheConfig, namespace: string, key: string, body: CacheRequestBody): Promise<CacheEntry> {
  if (body.value === undefined) throw new Error("Cache value is required for set operation");
  const ttl = clampTtl(body.ttlSeconds, config);
  const stale = clampStale(body.staleSeconds, config);
  const hash = await sha256Hex({ namespace, key, value: body.value });
  const expiresAt = addSeconds(ttl);
  const staleUntil = stale > 0 ? new Date(new Date(expiresAt).getTime() + stale * 1000).toISOString() : expiresAt;

  const payload = {
    namespace,
    cache_key: key,
    value: body.value,
    etag: weakEtag(hash),
    tags: body.tags ?? [],
    metadata: body.metadata ?? {},
    expires_at: expiresAt,
    stale_until: staleUntil,
    updated_at: nowIso(),
  };

  const { data, error } = await admin
    .from(config.tableName)
    .upsert(payload, { onConflict: "namespace,cache_key" })
    .select("namespace,cache_key,value,etag,tags,metadata,expires_at,stale_until,created_at,updated_at")
    .single<CacheEntry>();

  if (error) throw new Error(`Cache set failed: ${error.message}`);
  return data;
}

async function deleteEntry(admin: SupabaseClient, config: CacheConfig, namespace: string, key: string): Promise<number> {
  const { data, error } = await admin
    .from(config.tableName)
    .delete()
    .eq("namespace", namespace)
    .eq("cache_key", key)
    .select("cache_key");
  if (error) throw new Error(`Cache delete failed: ${error.message}`);
  return Array.isArray(data) ? data.length : 0;
}

async function invalidate(admin: SupabaseClient, config: CacheConfig, namespace: string, body: CacheRequestBody): Promise<number> {
  if (body.deleteByTags?.length) {
    const { data, error } = await admin
      .from(config.tableName)
      .delete()
      .eq("namespace", namespace)
      .overlaps("tags", body.deleteByTags)
      .select("cache_key");
    if (error) throw new Error(`Cache invalidate by tags failed: ${error.message}`);
    return Array.isArray(data) ? data.length : 0;
  }

  if (body.pattern) {
    const likePattern = body.pattern.replace(/\*/g, "%");
    const { data, error } = await admin
      .from(config.tableName)
      .delete()
      .eq("namespace", namespace)
      .like("cache_key", likePattern)
      .select("cache_key");
    if (error) throw new Error(`Cache invalidate by pattern failed: ${error.message}`);
    return Array.isArray(data) ? data.length : 0;
  }

  const { data, error } = await admin
    .from(config.tableName)
    .delete()
    .eq("namespace", namespace)
    .select("cache_key");
  if (error) throw new Error(`Cache namespace invalidate failed: ${error.message}`);
  return Array.isArray(data) ? data.length : 0;
}

async function purgeExpired(admin: SupabaseClient, config: CacheConfig, namespace: string): Promise<number> {
  const { data, error } = await admin
    .from(config.tableName)
    .delete()
    .eq("namespace", namespace)
    .not("stale_until", "is", null)
    .lte("stale_until", nowIso())
    .select("cache_key");
  if (error) throw new Error(`Cache purge expired failed: ${error.message}`);
  return Array.isArray(data) ? data.length : 0;
}

// =============================================================================
// Audit / metrics
// =============================================================================

async function audit(admin: SupabaseClient, config: CacheConfig, req: Request, event: AuditEvent): Promise<void> {
  if (!config.enableAudit) return;
  try {
    await admin.from(config.auditTableName).insert({
      ...event,
      ip_address: event.ip_address ?? getClientIp(req),
      user_agent: event.user_agent ?? req.headers.get("user-agent"),
      created_at: nowIso(),
    });
  } catch {
    // Audit must not fail cache path.
  }
}

async function metric(admin: SupabaseClient, config: CacheConfig, name: string, value: number, labels: Record<string, string>): Promise<void> {
  if (!config.enableMetrics) return;
  try {
    await admin.rpc("record_edge_metric", {
      p_metric_name: name,
      p_metric_value: value,
      p_labels: labels,
    });
  } catch {
    // Optional RPC. Ignore when not installed.
  }
}

// =============================================================================
// Handler
// =============================================================================

async function handleRequest(req: Request): Promise<Response> {
  const config = loadConfig();
  const headers = corsHeaders(req, config);
  const correlationId = getCorrelationId(req, config);
  const admin = serviceClient(config);

  if (req.method === "OPTIONS") return emptyResponse(204, headers);

  let body: CacheRequestBody = {};
  let operation: CacheOperation = inferOperation(req);
  let namespace = config.defaultNamespace;
  let key: string | null = null;

  try {
    if (!["GET", "POST", "DELETE"].includes(req.method)) {
      return errorResponse("Method not allowed", 405, operation, namespace, correlationId, headers, "METHOD_NOT_ALLOWED");
    }

    if (!assertAuthorized(req, config)) {
      return errorResponse("Unauthorized", 401, operation, namespace, correlationId, headers, "UNAUTHORIZED");
    }

    body = await readBody(req, config);
    operation = body.operation ?? operation;
    namespace = normalizeNamespace(body.namespace, config);
    key = body.key ? normalizeKey(body.key) : null;

    if (operation === "health") {
      return cacheResponse({
        ok: true,
        operation,
        status: "hit",
        namespace,
        metadata: {
          service: config.serviceName,
          environment: config.environment,
          tableName: config.tableName,
        },
        correlationId,
        timestamp: nowIso(),
      }, 200, headers);
    }

    if (operation === "get") {
      if (!key) throw new Error("Cache key is required for get operation");
      const entry = await getEntry(admin, config, namespace, key);
      if (!entry) {
        await metric(admin, config, "edge_cache_miss", 1, { namespace });
        await audit(admin, config, req, auditEvent(operation, "miss", namespace, key, correlationId, { reason: "not_found" }));
        return cacheResponse({ ok: true, operation, status: "miss", namespace, key, correlationId, timestamp: nowIso() }, 404, headers);
      }

      const expired = isExpired(entry);
      const staleAvailable = isStaleAvailable(entry);
      const ifNoneMatch = req.headers.get("if-none-match");

      if (!expired && ifNoneMatch && ifNoneMatch === entry.etag) {
        await metric(admin, config, "edge_cache_not_modified", 1, { namespace });
        return emptyResponse(304, { ...headers, etag: entry.etag, "x-cache": "HIT" });
      }

      if (!expired) {
        await metric(admin, config, "edge_cache_hit", 1, { namespace });
        await audit(admin, config, req, auditEvent(operation, "hit", namespace, key, correlationId));
        return cacheResponse({
          ok: true,
          operation,
          status: "hit",
          namespace,
          key,
          value: entry.value,
          etag: entry.etag,
          fresh: true,
          stale: false,
          expired: false,
          metadata: entry.metadata ?? {},
          correlationId,
          timestamp: nowIso(),
        }, 200, headers);
      }

      if (staleAvailable) {
        await metric(admin, config, "edge_cache_stale", 1, { namespace });
        await audit(admin, config, req, auditEvent(operation, "stale", namespace, key, correlationId));
        return cacheResponse({
          ok: true,
          operation,
          status: "stale",
          namespace,
          key,
          value: entry.value,
          etag: entry.etag,
          fresh: false,
          stale: true,
          expired: true,
          metadata: entry.metadata ?? {},
          correlationId,
          timestamp: nowIso(),
        }, 200, headers);
      }

      await metric(admin, config, "edge_cache_miss_expired", 1, { namespace });
      await audit(admin, config, req, auditEvent(operation, "miss", namespace, key, correlationId, { reason: "expired" }));
      return cacheResponse({ ok: true, operation, status: "miss", namespace, key, expired: true, correlationId, timestamp: nowIso() }, 404, headers);
    }

    if (operation === "set") {
      if (!key) throw new Error("Cache key is required for set operation");
      const entry = await setEntry(admin, config, namespace, key, body);
      await metric(admin, config, "edge_cache_set", 1, { namespace });
      await audit(admin, config, req, auditEvent(operation, "set", namespace, key, correlationId, { tags: body.tags ?? [] }));
      return cacheResponse({
        ok: true,
        operation,
        status: "set",
        namespace,
        key,
        etag: entry.etag,
        metadata: entry.metadata ?? {},
        correlationId,
        timestamp: nowIso(),
      }, 200, headers);
    }

    if (operation === "delete") {
      if (!key) throw new Error("Cache key is required for delete operation");
      const affected = await deleteEntry(admin, config, namespace, key);
      await metric(admin, config, "edge_cache_delete", affected, { namespace });
      await audit(admin, config, req, auditEvent(operation, "deleted", namespace, key, correlationId, { affectedRows: affected }));
      return cacheResponse({ ok: true, operation, status: "deleted", namespace, key, affectedRows: affected, correlationId, timestamp: nowIso() }, 200, headers);
    }

    if (operation === "invalidate") {
      const affected = await invalidate(admin, config, namespace, body);
      await metric(admin, config, "edge_cache_invalidate", affected, { namespace });
      await audit(admin, config, req, auditEvent(operation, "invalidated", namespace, key, correlationId, { affectedRows: affected, pattern: body.pattern, tags: body.deleteByTags }));
      return cacheResponse({ ok: true, operation, status: "invalidated", namespace, key, affectedRows: affected, correlationId, timestamp: nowIso() }, 200, headers);
    }

    if (operation === "purge_expired") {
      const affected = await purgeExpired(admin, config, namespace);
      await metric(admin, config, "edge_cache_purge_expired", affected, { namespace });
      await audit(admin, config, req, auditEvent(operation, "deleted", namespace, null, correlationId, { affectedRows: affected }));
      return cacheResponse({ ok: true, operation, status: "deleted", namespace, affectedRows: affected, correlationId, timestamp: nowIso() }, 200, headers);
    }

    return errorResponse("Unsupported cache operation", 400, operation, namespace, correlationId, headers, "UNSUPPORTED_OPERATION");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected cache error";
    await metric(admin, config, "edge_cache_error", 1, { namespace, operation });
    await audit(admin, config, req, auditEvent(operation, "error", namespace, key, correlationId, { error: message }));
    return errorResponse(message, 500, operation, namespace, correlationId, headers, "CACHE_EXECUTION_ERROR");
  }
}

function auditEvent(operation: CacheOperation, status: CacheStatus, namespace: string, key: string | null, correlationId: string, metadata: Record<string, unknown> = {}): AuditEvent {
  return {
    event_type: `cache_${status}`,
    operation,
    status,
    namespace,
    cache_key: key,
    correlation_id: correlationId,
    metadata,
  };
}

Deno.serve(handleRequest);
