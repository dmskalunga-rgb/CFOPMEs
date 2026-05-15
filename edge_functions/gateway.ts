// edge_functions/gateway.ts
// Enterprise API Gateway for Supabase/Deno Edge Functions

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";

type Json = Record<string, unknown>;

type RouteConfig = {
  path: string;
  targetUrl: string;
  methods: string[];
  authRequired: boolean;
  timeoutMs: number;
  rateLimit: {
    windowMs: number;
    maxRequests: number;
  };
};

type GatewayErrorBody = {
  requestId: string;
  status: "error";
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
  durationMs: number;
};

const CONFIG = {
  defaultTimeoutMs: 15_000,
  maxBodyBytes: 1_000_000,
  gatewayApiKey: Deno.env.get("GATEWAY_API_KEY") ?? "",
  internalSecret: Deno.env.get("INTERNAL_GATEWAY_SECRET") ?? "",

  routes: [
    {
      path: "/executor",
      targetUrl: Deno.env.get("EXECUTOR_URL") ?? "",
      methods: ["POST"],
      authRequired: true,
      timeoutMs: 20_000,
      rateLimit: {
        windowMs: 60_000,
        maxRequests: 120,
      },
    },
    {
      path: "/fraud/realtime",
      targetUrl: Deno.env.get("FRAUD_REALTIME_URL") ?? "",
      methods: ["POST"],
      authRequired: true,
      timeoutMs: 10_000,
      rateLimit: {
        windowMs: 60_000,
        maxRequests: 180,
      },
    },
    {
      path: "/health",
      targetUrl: "",
      methods: ["GET"],
      authRequired: false,
      timeoutMs: 3_000,
      rateLimit: {
        windowMs: 60_000,
        maxRequests: 300,
      },
    },
  ] satisfies RouteConfig[],
};

const rateBuckets = new Map<string, { count: number; resetAt: number }>();
const idempotencyCache = new Map<string, Response>();
const circuitBreaker = new Map<
  string,
  {
    failures: number;
    openedUntil: number;
  }
>();

class GatewayError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 500,
    public details?: unknown,
  ) {
    super(message);
    this.name = "GatewayError";
  }
}

function now(): number {
  return Date.now();
}

function uuid(): string {
  return crypto.randomUUID();
}

function corsHeaders(): HeadersInit {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "access-control-allow-headers":
      "authorization, content-type, x-request-id, x-idempotency-key",
  };
}

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...corsHeaders(),
      ...headers,
    },
  });
}

async function audit(
  level: "info" | "warn" | "error",
  event: string,
  data: Json,
): Promise<void> {
  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    level,
    event,
    ...data,
  }));
}

async function metric(
  name: string,
  value: number,
  tags: Record<string, string> = {},
): Promise<void> {
  console.log(JSON.stringify({
    type: "metric",
    name,
    value,
    tags,
    timestamp: new Date().toISOString(),
  }));
}

function clientKey(req: Request): string {
  const forwarded = req.headers.get("x-forwarded-for");
  return forwarded?.split(",")[0]?.trim() ||
    req.headers.get("cf-connecting-ip") ||
    "unknown";
}

function findRoute(req: Request): RouteConfig {
  const url = new URL(req.url);
  const route = CONFIG.routes.find((item) => url.pathname.endsWith(item.path));

  if (!route) {
    throw new GatewayError("ROUTE_NOT_FOUND", "Rota não encontrada.", 404, {
      path: url.pathname,
    });
  }

  if (!route.methods.includes(req.method)) {
    throw new GatewayError("METHOD_NOT_ALLOWED", "Método não permitido.", 405, {
      allowedMethods: route.methods,
    });
  }

  return route;
}

function enforceRateLimit(req: Request, route: RouteConfig): void {
  const key = `${route.path}:${clientKey(req)}`;
  const current = now();
  const bucket = rateBuckets.get(key);

  if (!bucket || bucket.resetAt <= current) {
    rateBuckets.set(key, {
      count: 1,
      resetAt: current + route.rateLimit.windowMs,
    });
    return;
  }

  bucket.count++;

  if (bucket.count > route.rateLimit.maxRequests) {
    throw new GatewayError("RATE_LIMITED", "Limite de requisições excedido.", 429, {
      limit: route.rateLimit.maxRequests,
      resetAt: new Date(bucket.resetAt).toISOString(),
    });
  }
}

function authenticate(req: Request, route: RouteConfig): void {
  if (!route.authRequired) return;
  if (!CONFIG.gatewayApiKey) return;

  const token = (req.headers.get("authorization") ?? "")
    .replace(/^Bearer\s+/i, "")
    .trim();

  if (token !== CONFIG.gatewayApiKey) {
    throw new GatewayError("UNAUTHORIZED", "Credencial inválida.", 401);
  }
}

async function checkPayloadSize(req: Request): Promise<void> {
  const length = Number(req.headers.get("content-length") ?? "0");

  if (length > CONFIG.maxBodyBytes) {
    throw new GatewayError("PAYLOAD_TOO_LARGE", "Payload excede o limite.", 413, {
      maxBodyBytes: CONFIG.maxBodyBytes,
    });
  }
}

function assertTarget(route: RouteConfig): void {
  if (route.path === "/health") return;

  if (!route.targetUrl) {
    throw new GatewayError(
      "TARGET_NOT_CONFIGURED",
      `Target não configurado para rota ${route.path}.`,
      500,
    );
  }
}

function checkCircuit(route: RouteConfig): void {
  const state = circuitBreaker.get(route.path);

  if (!state) return;

  if (state.openedUntil > now()) {
    throw new GatewayError("CIRCUIT_OPEN", "Serviço temporariamente indisponível.", 503, {
      route: route.path,
      retryAfterMs: state.openedUntil - now(),
    });
  }
}

function registerFailure(route: RouteConfig): void {
  const current = circuitBreaker.get(route.path) ?? {
    failures: 0,
    openedUntil: 0,
  };

  current.failures++;

  if (current.failures >= 5) {
    current.openedUntil = now() + 30_000;
  }

  circuitBreaker.set(route.path, current);
}

function registerSuccess(route: RouteConfig): void {
  circuitBreaker.delete(route.path);
}

async function withTimeout(
  timeoutMs: number,
  execute: (signal: AbortSignal) => Promise<Response>,
): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await execute(controller.signal);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new GatewayError("UPSTREAM_TIMEOUT", "Timeout no serviço destino.", 504, {
        timeoutMs,
      });
    }

    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function cloneHeaders(req: Request, requestId: string): Headers {
  const headers = new Headers(req.headers);

  headers.set("x-request-id", requestId);
  headers.set("x-gateway", "edge-gateway");

  if (CONFIG.internalSecret) {
    headers.set("x-internal-gateway-secret", CONFIG.internalSecret);
  }

  headers.delete("host");
  headers.delete("content-length");

  return headers;
}

async function proxyRequest(
  req: Request,
  route: RouteConfig,
  requestId: string,
): Promise<Response> {
  const sourceUrl = new URL(req.url);
  const targetUrl = new URL(route.targetUrl);

  targetUrl.search = sourceUrl.search;

  const body = req.method === "GET" || req.method === "HEAD"
    ? undefined
    : await req.arrayBuffer();

  const response = await withTimeout(route.timeoutMs, async (signal) => {
    return await fetch(targetUrl.toString(), {
      method: req.method,
      headers: cloneHeaders(req, requestId),
      body,
      signal,
    });
  });

  const responseHeaders = new Headers(response.headers);

  responseHeaders.set("x-request-id", requestId);
  responseHeaders.set("x-gateway", "edge-gateway");

  for (const [key, value] of Object.entries(corsHeaders())) {
    responseHeaders.set(key, String(value));
  }

  return new Response(response.body, {
    status: response.status,
    headers: responseHeaders,
  });
}

function healthResponse(requestId: string, startedAt: number): Response {
  return json({
    requestId,
    status: "ok",
    service: "edge-gateway",
    timestamp: new Date().toISOString(),
    durationMs: now() - startedAt,
    routes: CONFIG.routes.map((route) => ({
      path: route.path,
      methods: route.methods,
      configured: route.path === "/health" || Boolean(route.targetUrl),
      authRequired: route.authRequired,
    })),
  });
}

function normalizeError(error: unknown): GatewayError {
  if (error instanceof GatewayError) return error;

  if (error instanceof Error) {
    return new GatewayError("INTERNAL_ERROR", error.message, 500);
  }

  return new GatewayError("UNKNOWN_ERROR", "Erro desconhecido.", 500);
}

async function handler(req: Request): Promise<Response> {
  const startedAt = now();
  const requestId = req.headers.get("x-request-id") || uuid();

  try {
    if (req.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(),
      });
    }

    await checkPayloadSize(req);

    const route = findRoute(req);

    enforceRateLimit(req, route);
    authenticate(req, route);
    assertTarget(route);
    checkCircuit(route);

    if (route.path === "/health") {
      return healthResponse(requestId, startedAt);
    }

    const idempotencyKey = req.headers.get("x-idempotency-key");
    const cacheKey = idempotencyKey
      ? `${route.path}:${idempotencyKey}`
      : "";

    if (cacheKey && idempotencyCache.has(cacheKey)) {
      const cached = idempotencyCache.get(cacheKey)!;

      return new Response(await cached.clone().arrayBuffer(), {
        status: cached.status,
        headers: {
          ...Object.fromEntries(cached.headers.entries()),
          "x-idempotent-replay": "true",
        },
      });
    }

    await audit("info", "gateway.request.started", {
      requestId,
      route: route.path,
      method: req.method,
      client: clientKey(req),
    });

    const response = await proxyRequest(req, route, requestId);

    if (response.status >= 500) {
      registerFailure(route);
    } else {
      registerSuccess(route);
    }

    if (cacheKey && response.ok) {
      idempotencyCache.set(cacheKey, response.clone());
    }

    await metric("gateway.request", 1, {
      route: route.path,
      status: String(response.status),
    });

    await audit("info", "gateway.request.finished", {
      requestId,
      route: route.path,
      status: response.status,
      durationMs: now() - startedAt,
    });

    return response;
  } catch (error) {
    const err = normalizeError(error);

    await audit("error", "gateway.request.failed", {
      requestId,
      code: err.code,
      message: err.message,
      status: err.status,
      durationMs: now() - startedAt,
    });

    const body: GatewayErrorBody = {
      requestId,
      status: "error",
      error: {
        code: err.code,
        message: err.message,
        details: err.details,
      },
      durationMs: now() - startedAt,
    };

    return json(body, err.status, {
      "x-request-id": requestId,
    });
  }
}

serve(handler);