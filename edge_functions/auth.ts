/**
 * edge_functions/auth.ts
 *
 * Enterprise-grade Supabase Edge Function for authentication, authorization,
 * token validation, API-key validation, RBAC/ABAC policy checks and audit logging.
 *
 * Runtime: Deno / Supabase Edge Functions
 *
 * Supported capabilities:
 * - CORS-safe request handling
 * - Bearer JWT validation using Supabase Auth user endpoint
 * - API key validation via hashed keys stored in Postgres
 * - RBAC role checks
 * - ABAC attribute checks
 * - Optional route/action/resource policy evaluation
 * - Rate-limit hooks backed by database RPC/table
 * - Request correlation IDs
 * - Structured JSON responses
 * - Secure error responses without leaking secrets
 * - Audit event persistence
 * - Health endpoint
 *
 * Suggested tables/RPCs:
 * - api_keys(id, key_hash, name, subject_id, roles text[], scopes text[], active bool,
 *            expires_at timestamptz, metadata jsonb)
 * - auth_audit_events(id, event_type, subject_id, actor_type, status, resource,
 *                     action, ip_address, user_agent, correlation_id, metadata jsonb,
 *                     created_at timestamptz)
 * - Optional RPC: consume_rate_limit(subject text, bucket text, max_requests int,
 *                                    window_seconds int) returns jsonb
 */

import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// =============================================================================
// Types
// =============================================================================

type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
type JsonObject = { [key: string]: JsonValue };

type AuthMode = "jwt" | "api_key" | "anonymous";
type ActorType = "user" | "service" | "anonymous";
type AuthStatus = "allowed" | "denied" | "error";

type RoleCheckMode = "any" | "all";

interface AuthConfig {
  supabaseUrl: string;
  supabaseAnonKey: string;
  supabaseServiceRoleKey: string;
  allowedOrigins: string[];
  allowCredentials: boolean;
  defaultRateLimitMaxRequests: number;
  defaultRateLimitWindowSeconds: number;
  apiKeyHeader: string;
  correlationHeader: string;
  enableAudit: boolean;
  enableRateLimit: boolean;
  enableApiKeyAuth: boolean;
  enableJwtAuth: boolean;
  apiKeyHashPepper: string;
  environment: string;
  serviceName: string;
}

interface AuthRequestBody {
  resource?: string;
  action?: string;
  requiredRoles?: string[];
  requiredScopes?: string[];
  roleCheckMode?: RoleCheckMode;
  attributes?: Record<string, unknown>;
  context?: Record<string, unknown>;
  allowAnonymous?: boolean;
}

interface AuthIdentity {
  authenticated: boolean;
  mode: AuthMode;
  actorType: ActorType;
  subjectId: string | null;
  email?: string | null;
  roles: string[];
  scopes: string[];
  attributes: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

interface AuthDecision {
  allowed: boolean;
  status: AuthStatus;
  reason: string;
  identity: AuthIdentity;
  resource?: string;
  action?: string;
  correlationId: string;
  metadata?: Record<string, unknown>;
}

interface ApiKeyRecord {
  id: string;
  key_hash: string;
  name?: string | null;
  subject_id?: string | null;
  roles?: string[] | null;
  scopes?: string[] | null;
  active?: boolean | null;
  expires_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

interface AuditEvent {
  event_type: string;
  subject_id: string | null;
  actor_type: ActorType;
  status: AuthStatus;
  resource?: string | null;
  action?: string | null;
  ip_address?: string | null;
  user_agent?: string | null;
  correlation_id: string;
  metadata: Record<string, unknown>;
}

// =============================================================================
// Constants
// =============================================================================

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
};

const DEFAULT_ALLOWED_HEADERS = [
  "authorization",
  "apikey",
  "content-type",
  "x-client-info",
  "x-correlation-id",
  "x-api-key",
  "x-request-id",
].join(", ");

const DEFAULT_ALLOWED_METHODS = "GET,POST,OPTIONS";

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
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function loadConfig(): AuthConfig {
  const config: AuthConfig = {
    supabaseUrl: getEnv("SUPABASE_URL"),
    supabaseAnonKey: getEnv("SUPABASE_ANON_KEY"),
    supabaseServiceRoleKey: getEnv("SUPABASE_SERVICE_ROLE_KEY"),
    allowedOrigins: getListEnv("AUTH_ALLOWED_ORIGINS", ["*"]),
    allowCredentials: getBooleanEnv("AUTH_ALLOW_CREDENTIALS", true),
    defaultRateLimitMaxRequests: getNumberEnv("AUTH_RATE_LIMIT_MAX_REQUESTS", 300),
    defaultRateLimitWindowSeconds: getNumberEnv("AUTH_RATE_LIMIT_WINDOW_SECONDS", 60),
    apiKeyHeader: getEnv("AUTH_API_KEY_HEADER", "x-api-key").toLowerCase(),
    correlationHeader: getEnv("AUTH_CORRELATION_HEADER", "x-correlation-id").toLowerCase(),
    enableAudit: getBooleanEnv("AUTH_ENABLE_AUDIT", true),
    enableRateLimit: getBooleanEnv("AUTH_ENABLE_RATE_LIMIT", false),
    enableApiKeyAuth: getBooleanEnv("AUTH_ENABLE_API_KEY", true),
    enableJwtAuth: getBooleanEnv("AUTH_ENABLE_JWT", true),
    apiKeyHashPepper: getEnv("AUTH_API_KEY_HASH_PEPPER", ""),
    environment: getEnv("APP_ENV", "development"),
    serviceName: getEnv("APP_SERVICE_NAME", "edge-auth"),
  };

  if (!config.supabaseUrl) throw new Error("SUPABASE_URL is required");
  if (!config.supabaseAnonKey) throw new Error("SUPABASE_ANON_KEY is required");
  if (!config.supabaseServiceRoleKey) throw new Error("SUPABASE_SERVICE_ROLE_KEY is required");

  return config;
}

// =============================================================================
// Response helpers
// =============================================================================

function nowIso(): string {
  return new Date().toISOString();
}

function jsonResponse(
  payload: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: {
      ...JSON_HEADERS,
      ...headers,
    },
  });
}

function errorResponse(
  message: string,
  status: number,
  correlationId: string,
  headers: Record<string, string>,
  code = "AUTH_ERROR",
): Response {
  return jsonResponse(
    {
      ok: false,
      error: {
        code,
        message,
      },
      correlationId,
      timestamp: nowIso(),
    },
    status,
    headers,
  );
}

function successResponse(
  decision: AuthDecision,
  headers: Record<string, string>,
): Response {
  return jsonResponse(
    {
      ok: true,
      allowed: decision.allowed,
      status: decision.status,
      reason: decision.reason,
      identity: sanitizeIdentity(decision.identity),
      resource: decision.resource ?? null,
      action: decision.action ?? null,
      correlationId: decision.correlationId,
      metadata: decision.metadata ?? {},
      timestamp: nowIso(),
    },
    decision.allowed ? 200 : 403,
    headers,
  );
}

function corsHeaders(req: Request, config: AuthConfig): Record<string, string> {
  const origin = req.headers.get("origin") ?? "";
  const allowAll = config.allowedOrigins.includes("*");
  const allowedOrigin = allowAll ? "*" : config.allowedOrigins.includes(origin) ? origin : config.allowedOrigins[0] ?? "";

  const headers: Record<string, string> = {
    "access-control-allow-origin": allowedOrigin,
    "access-control-allow-methods": DEFAULT_ALLOWED_METHODS,
    "access-control-allow-headers": DEFAULT_ALLOWED_HEADERS,
    "access-control-max-age": "86400",
    "vary": "Origin",
  };

  if (config.allowCredentials && allowedOrigin !== "*") {
    headers["access-control-allow-credentials"] = "true";
  }

  return headers;
}

// =============================================================================
// Request helpers
// =============================================================================

function getCorrelationId(req: Request, config: AuthConfig): string {
  return (
    req.headers.get(config.correlationHeader) ??
    req.headers.get("x-request-id") ??
    crypto.randomUUID()
  );
}

function getClientIp(req: Request): string | null {
  return (
    req.headers.get("cf-connecting-ip") ??
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    req.headers.get("x-real-ip") ??
    null
  );
}

async function readBody(req: Request): Promise<AuthRequestBody> {
  if (req.method === "GET") {
    const url = new URL(req.url);
    return {
      resource: url.searchParams.get("resource") ?? undefined,
      action: url.searchParams.get("action") ?? undefined,
      requiredRoles: splitCsv(url.searchParams.get("requiredRoles")),
      requiredScopes: splitCsv(url.searchParams.get("requiredScopes")),
      roleCheckMode: (url.searchParams.get("roleCheckMode") as RoleCheckMode | null) ?? "any",
      allowAnonymous: url.searchParams.get("allowAnonymous") === "true",
    };
  }

  const contentType = req.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return {};

  try {
    const parsed = await req.json();
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as AuthRequestBody;
  } catch {
    return {};
  }
}

function splitCsv(value: string | null): string[] | undefined {
  if (!value) return undefined;
  const items = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : undefined;
}

function bearerToken(req: Request): string | null {
  const auth = req.headers.get("authorization") ?? "";
  const match = auth.match(/^Bearer\s+(.+)$/i);
  return match?.[1]?.trim() ?? null;
}

function apiKey(req: Request, config: AuthConfig): string | null {
  return (
    req.headers.get(config.apiKeyHeader) ??
    req.headers.get("apikey") ??
    req.headers.get("x-api-key") ??
    null
  );
}

// =============================================================================
// Supabase clients
// =============================================================================

function serviceClient(config: AuthConfig): SupabaseClient {
  return createClient(config.supabaseUrl, config.supabaseServiceRoleKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
    global: {
      headers: {
        "x-application-name": config.serviceName,
      },
    },
  });
}

function anonClient(config: AuthConfig, token?: string): SupabaseClient {
  return createClient(config.supabaseUrl, config.supabaseAnonKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
    global: {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    },
  });
}

// =============================================================================
// Auth logic
// =============================================================================

async function authenticate(
  req: Request,
  config: AuthConfig,
  admin: SupabaseClient,
): Promise<AuthIdentity> {
  const token = bearerToken(req);
  if (config.enableJwtAuth && token) {
    const identity = await authenticateJwt(config, token);
    if (identity.authenticated) return identity;
  }

  const key = apiKey(req, config);
  if (config.enableApiKeyAuth && key) {
    const identity = await authenticateApiKey(admin, key, config);
    if (identity.authenticated) return identity;
  }

  return anonymousIdentity();
}

async function authenticateJwt(config: AuthConfig, token: string): Promise<AuthIdentity> {
  try {
    const client = anonClient(config, token);
    const { data, error } = await client.auth.getUser(token);
    if (error || !data?.user) return anonymousIdentity();

    const user = data.user;
    const appMetadata = (user.app_metadata ?? {}) as Record<string, unknown>;
    const userMetadata = (user.user_metadata ?? {}) as Record<string, unknown>;

    return {
      authenticated: true,
      mode: "jwt",
      actorType: "user",
      subjectId: user.id,
      email: user.email ?? null,
      roles: normalizeStringArray(appMetadata.roles ?? appMetadata.role ?? userMetadata.roles),
      scopes: normalizeStringArray(appMetadata.scopes ?? userMetadata.scopes),
      attributes: {
        email: user.email ?? null,
        phone: user.phone ?? null,
        aud: user.aud,
        ...userMetadata,
      },
      metadata: {
        provider: "supabase_auth",
        app_metadata: appMetadata,
      },
    };
  } catch {
    return anonymousIdentity();
  }
}

async function authenticateApiKey(
  admin: SupabaseClient,
  rawKey: string,
  config: AuthConfig,
): Promise<AuthIdentity> {
  const keyHash = await sha256Hex(`${rawKey}${config.apiKeyHashPepper}`);
  const { data, error } = await admin
    .from("api_keys")
    .select("id,key_hash,name,subject_id,roles,scopes,active,expires_at,metadata")
    .eq("key_hash", keyHash)
    .limit(1)
    .maybeSingle<ApiKeyRecord>();

  if (error || !data) return anonymousIdentity();
  if (data.active === false) return anonymousIdentity();
  if (data.expires_at && new Date(data.expires_at).getTime() <= Date.now()) return anonymousIdentity();

  return {
    authenticated: true,
    mode: "api_key",
    actorType: "service",
    subjectId: data.subject_id ?? data.id,
    email: null,
    roles: normalizeStringArray(data.roles),
    scopes: normalizeStringArray(data.scopes),
    attributes: {
      api_key_id: data.id,
      api_key_name: data.name ?? null,
    },
    metadata: data.metadata ?? {},
  };
}

function anonymousIdentity(): AuthIdentity {
  return {
    authenticated: false,
    mode: "anonymous",
    actorType: "anonymous",
    subjectId: null,
    email: null,
    roles: [],
    scopes: [],
    attributes: {},
    metadata: {},
  };
}

function normalizeStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === "string") {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

// =============================================================================
// Authorization logic
// =============================================================================

async function authorize(
  req: Request,
  body: AuthRequestBody,
  identity: AuthIdentity,
  config: AuthConfig,
  admin: SupabaseClient,
  correlationId: string,
): Promise<AuthDecision> {
  const resource = body.resource ?? "default";
  const action = body.action ?? "access";

  if (!identity.authenticated && !body.allowAnonymous) {
    return deny("Authentication required", identity, resource, action, correlationId);
  }

  if (config.enableRateLimit) {
    const rateAllowed = await consumeRateLimit(admin, identity, resource, action, config);
    if (!rateAllowed) {
      return deny("Rate limit exceeded", identity, resource, action, correlationId, {
        rateLimited: true,
      });
    }
  }

  const requiredRoles = body.requiredRoles ?? [];
  const requiredScopes = body.requiredScopes ?? [];
  const roleMode = body.roleCheckMode ?? "any";

  if (requiredRoles.length && !hasRoles(identity.roles, requiredRoles, roleMode)) {
    return deny("Required role missing", identity, resource, action, correlationId, {
      requiredRoles,
      roleCheckMode: roleMode,
    });
  }

  if (requiredScopes.length && !hasScopes(identity.scopes, requiredScopes)) {
    return deny("Required scope missing", identity, resource, action, correlationId, {
      requiredScopes,
    });
  }

  const abacAllowed = evaluateAttributes(identity, body.attributes ?? {}, body.context ?? {});
  if (!abacAllowed) {
    return deny("Attribute policy denied", identity, resource, action, correlationId);
  }

  const policyAllowed = await evaluatePolicy(admin, identity, resource, action, body.context ?? {});
  if (!policyAllowed.allowed) {
    return deny(policyAllowed.reason, identity, resource, action, correlationId, policyAllowed.metadata);
  }

  return {
    allowed: true,
    status: "allowed",
    reason: "Access granted",
    identity,
    resource,
    action,
    correlationId,
    metadata: {
      authMode: identity.mode,
      roleCount: identity.roles.length,
      scopeCount: identity.scopes.length,
    },
  };
}

function deny(
  reason: string,
  identity: AuthIdentity,
  resource: string,
  action: string,
  correlationId: string,
  metadata: Record<string, unknown> = {},
): AuthDecision {
  return {
    allowed: false,
    status: "denied",
    reason,
    identity,
    resource,
    action,
    correlationId,
    metadata,
  };
}

function hasRoles(actual: string[], required: string[], mode: RoleCheckMode): boolean {
  const actualSet = new Set(actual.map((role) => role.toLowerCase()));
  const normalizedRequired = required.map((role) => role.toLowerCase());
  if (mode === "all") return normalizedRequired.every((role) => actualSet.has(role));
  return normalizedRequired.some((role) => actualSet.has(role));
}

function hasScopes(actual: string[], required: string[]): boolean {
  const actualSet = new Set(actual.map((scope) => scope.toLowerCase()));
  return required.every((scope) => actualSet.has(scope.toLowerCase()));
}

function evaluateAttributes(
  identity: AuthIdentity,
  requiredAttributes: Record<string, unknown>,
  context: Record<string, unknown>,
): boolean {
  for (const [key, expected] of Object.entries(requiredAttributes)) {
    const actual = identity.attributes[key] ?? identity.metadata[key] ?? context[key];
    if (Array.isArray(expected)) {
      if (!expected.includes(actual)) return false;
      continue;
    }
    if (expected !== actual) return false;
  }
  return true;
}

async function evaluatePolicy(
  admin: SupabaseClient,
  identity: AuthIdentity,
  resource: string,
  action: string,
  context: Record<string, unknown>,
): Promise<{ allowed: boolean; reason: string; metadata?: Record<string, unknown> }> {
  try {
    const { data, error } = await admin.rpc("evaluate_auth_policy", {
      p_subject_id: identity.subjectId,
      p_actor_type: identity.actorType,
      p_roles: identity.roles,
      p_scopes: identity.scopes,
      p_resource: resource,
      p_action: action,
      p_context: context,
    });

    if (error) {
      // Missing RPC should not break deployments that rely only on inline checks.
      if (String(error.message ?? "").includes("evaluate_auth_policy")) {
        return { allowed: true, reason: "No external policy configured" };
      }
      return { allowed: false, reason: "Policy evaluation error", metadata: { error: error.message } };
    }

    if (data == null) return { allowed: true, reason: "No external policy response" };
    if (typeof data === "boolean") return { allowed: data, reason: data ? "Policy allowed" : "Policy denied" };
    if (typeof data === "object") {
      const payload = data as Record<string, unknown>;
      return {
        allowed: payload.allowed !== false,
        reason: String(payload.reason ?? (payload.allowed === false ? "Policy denied" : "Policy allowed")),
        metadata: payload as Record<string, unknown>,
      };
    }

    return { allowed: true, reason: "Policy allowed" };
  } catch {
    return { allowed: true, reason: "Policy evaluation unavailable" };
  }
}

async function consumeRateLimit(
  admin: SupabaseClient,
  identity: AuthIdentity,
  resource: string,
  action: string,
  config: AuthConfig,
): Promise<boolean> {
  const subject = identity.subjectId ?? getAnonymousBucket(resource, action);
  try {
    const { data, error } = await admin.rpc("consume_rate_limit", {
      p_subject: subject,
      p_bucket: `${resource}:${action}`,
      p_max_requests: config.defaultRateLimitMaxRequests,
      p_window_seconds: config.defaultRateLimitWindowSeconds,
    });

    if (error) return true;
    if (typeof data === "boolean") return data;
    if (data && typeof data === "object") {
      const payload = data as Record<string, unknown>;
      return payload.allowed !== false;
    }
    return true;
  } catch {
    return true;
  }
}

function getAnonymousBucket(resource: string, action: string): string {
  return `anonymous:${resource}:${action}`;
}

// =============================================================================
// Audit
// =============================================================================

async function audit(
  admin: SupabaseClient,
  config: AuthConfig,
  req: Request,
  decision: AuthDecision,
): Promise<void> {
  if (!config.enableAudit) return;

  const event: AuditEvent = {
    event_type: decision.allowed ? "auth_allowed" : "auth_denied",
    subject_id: decision.identity.subjectId,
    actor_type: decision.identity.actorType,
    status: decision.status,
    resource: decision.resource ?? null,
    action: decision.action ?? null,
    ip_address: getClientIp(req),
    user_agent: req.headers.get("user-agent"),
    correlation_id: decision.correlationId,
    metadata: {
      reason: decision.reason,
      auth_mode: decision.identity.mode,
      roles: decision.identity.roles,
      scopes: decision.identity.scopes,
      environment: config.environment,
      service_name: config.serviceName,
      decision_metadata: decision.metadata ?? {},
    },
  };

  try {
    await admin.from("auth_audit_events").insert(event);
  } catch {
    // Never fail auth path because audit failed.
  }
}

// =============================================================================
// Crypto helpers
// =============================================================================

async function sha256Hex(value: string): Promise<string> {
  const encoded = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function sanitizeIdentity(identity: AuthIdentity): Record<string, unknown> {
  return {
    authenticated: identity.authenticated,
    mode: identity.mode,
    actorType: identity.actorType,
    subjectId: identity.subjectId,
    email: identity.email ?? null,
    roles: identity.roles,
    scopes: identity.scopes,
    attributes: identity.attributes,
    metadata: identity.metadata,
  };
}

// =============================================================================
// Handler
// =============================================================================

async function handleRequest(req: Request): Promise<Response> {
  const config = loadConfig();
  const headers = corsHeaders(req, config);
  const correlationId = getCorrelationId(req, config);

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers });
  }

  if (!['GET', 'POST'].includes(req.method)) {
    return errorResponse("Method not allowed", 405, correlationId, headers, "METHOD_NOT_ALLOWED");
  }

  const url = new URL(req.url);
  if (url.pathname.endsWith("/health") || url.searchParams.get("health") === "true") {
    return jsonResponse(
      {
        ok: true,
        service: config.serviceName,
        environment: config.environment,
        timestamp: nowIso(),
      },
      200,
      headers,
    );
  }

  const admin = serviceClient(config);

  try {
    const body = await readBody(req);
    const identity = await authenticate(req, config, admin);
    const decision = await authorize(req, body, identity, config, admin, correlationId);
    await audit(admin, config, req, decision);
    return successResponse(decision, headers);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected authentication error";
    const identity = anonymousIdentity();
    const decision = deny("Authentication service error", identity, "unknown", "unknown", correlationId, {
      error: message,
    });
    await audit(admin, config, req, decision);
    return errorResponse("Authentication service error", 500, correlationId, headers, "AUTH_SERVICE_ERROR");
  }
}

Deno.serve(handleRequest);
