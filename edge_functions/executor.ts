
// edge_functions/executor.ts
// Enterprise Edge Function Executor
// Runtime: Deno / Supabase Edge Functions

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

type ExecutorStatus =
  | "accepted"
  | "running"
  | "success"
  | "failed"
  | "timeout"
  | "rejected"
  | "rate_limited";

type ExecutorRequest = {
  taskId?: string;
  action: string;
  payload?: Record<string, JsonValue>;
  metadata?: Record<string, JsonValue>;
  idempotencyKey?: string;
  priority?: "low" | "normal" | "high" | "critical";
  timeoutMs?: number;
  retries?: number;
};

type ExecutorResponse = {
  requestId: string;
  taskId: string;
  status: ExecutorStatus;
  action: string;
  durationMs: number;
  result?: JsonValue;
  error?: {
    code: string;
    message: string;
    details?: JsonValue;
  };
};

type ExecutionContext = {
  requestId: string;
  taskId: string;
  action: string;
  startedAt: number;
  deadlineAt: number;
  attempt: number;
  maxRetries: number;
  signal: AbortSignal;
  payload: Record<string, JsonValue>;
  metadata: Record<string, JsonValue>;
};

type ExecutorHandler = (ctx: ExecutionContext) => Promise<JsonValue>;

const CONFIG = {
  maxPayloadBytes: 512_000,
  defaultTimeoutMs: 15_000,
  maxTimeoutMs: 60_000,
  defaultRetries: 1,
  maxRetries: 5,
  rateLimitWindowMs: 60_000,
  rateLimitMaxRequests: 120,
  allowedOrigins: [
    Deno.env.get("PUBLIC_APP_URL") ?? "*",
  ],
};

const memoryRateLimit = new Map<string, { count: number; resetAt: number }>();
const idempotencyStore = new Map<string, ExecutorResponse>();

class ExecutorError extends Error {
  code: string;
  status: number;
  details?: JsonValue;

  constructor(
    code: string,
    message: string,
    status = 500,
    details?: JsonValue,
  ) {
    super(message);
    this.name = "ExecutorError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

function jsonResponse(
  body: JsonValue,
  status = 200,
  extraHeaders: HeadersInit = {},
): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...corsHeaders(),
      ...extraHeaders,
    },
  });
}

function corsHeaders(): HeadersInit {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers":
      "authorization, content-type, x-request-id, x-idempotency-key",
  };
}

function createRequestId(): string {
  return crypto.randomUUID();
}

function now(): number {
  return Date.now();
}

function sanitizeAction(action: unknown): string {
  if (typeof action !== "string" || !action.trim()) {
    throw new ExecutorError(
      "INVALID_ACTION",
      "O campo action é obrigatório.",
      400,
    );
  }

  const normalized = action.trim();

  if (!/^[a-zA-Z0-9._:-]{3,120}$/.test(normalized)) {
    throw new ExecutorError(
      "INVALID_ACTION_FORMAT",
      "Formato inválido para action.",
      400,
    );
  }

  return normalized;
}

async function readJsonBody(req: Request): Promise<ExecutorRequest> {
  const contentLength = Number(req.headers.get("content-length") ?? "0");

  if (contentLength > CONFIG.maxPayloadBytes) {
    throw new ExecutorError(
      "PAYLOAD_TOO_LARGE",
      "Payload excede o limite permitido.",
      413,
      { maxPayloadBytes: CONFIG.maxPayloadBytes },
    );
  }

  try {
    return await req.json();
  } catch {
    throw new ExecutorError(
      "INVALID_JSON",
      "Body precisa ser um JSON válido.",
      400,
    );
  }
}

function getClientKey(req: Request): string {
  const forwardedFor = req.headers.get("x-forwarded-for");
  const ip = forwardedFor?.split(",")[0]?.trim();

  return ip || req.headers.get("cf-connecting-ip") || "unknown";
}

function enforceRateLimit(req: Request): void {
  const key = getClientKey(req);
  const current = now();
  const bucket = memoryRateLimit.get(key);

  if (!bucket || bucket.resetAt <= current) {
    memoryRateLimit.set(key, {
      count: 1,
      resetAt: current + CONFIG.rateLimitWindowMs,
    });
    return;
  }

  bucket.count++;

  if (bucket.count > CONFIG.rateLimitMaxRequests) {
    throw new ExecutorError(
      "RATE_LIMITED",
      "Limite de requisições excedido.",
      429,
      {
        limit: CONFIG.rateLimitMaxRequests,
        resetAt: new Date(bucket.resetAt).toISOString(),
      },
    );
  }
}

async function authenticate(req: Request): Promise<void> {
  const expectedApiKey = Deno.env.get("EDGE_EXECUTOR_API_KEY");

  if (!expectedApiKey) {
    return;
  }

  const auth = req.headers.get("authorization") ?? "";
  const token = auth.replace(/^Bearer\s+/i, "").trim();

  if (!token || token !== expectedApiKey) {
    throw new ExecutorError(
      "UNAUTHORIZED",
      "Credencial inválida ou ausente.",
      401,
    );
  }
}

function normalizeExecutorRequest(
  body: ExecutorRequest,
  req: Request,
): Required<ExecutorRequest> {
  const action = sanitizeAction(body.action);
  const requestId = req.headers.get("x-request-id") || createRequestId();

  const timeoutMs = Math.min(
    Math.max(Number(body.timeoutMs ?? CONFIG.defaultTimeoutMs), 1_000),
    CONFIG.maxTimeoutMs,
  );

  const retries = Math.min(
    Math.max(Number(body.retries ?? CONFIG.defaultRetries), 0),
    CONFIG.maxRetries,
  );

  return {
    taskId: body.taskId || requestId,
    action,
    payload: body.payload ?? {},
    metadata: {
      ...(body.metadata ?? {}),
      requestId,
      userAgent: req.headers.get("user-agent") ?? "unknown",
    },
    idempotencyKey:
      body.idempotencyKey ||
      req.headers.get("x-idempotency-key") ||
      "",
    priority: body.priority ?? "normal",
    timeoutMs,
    retries,
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function backoffDelay(attempt: number): number {
  const base = 250;
  const max = 5_000;
  const jitter = Math.floor(Math.random() * 100);

  return Math.min(base * 2 ** attempt + jitter, max);
}

async function withTimeout<T>(
  timeoutMs: number,
  fn: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fn(controller.signal);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ExecutorError(
        "EXECUTION_TIMEOUT",
        "Execução excedeu o tempo limite.",
        504,
        { timeoutMs },
      );
    }

    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function auditLog(
  level: "info" | "warn" | "error",
  event: string,
  data: Record<string, JsonValue>,
): Promise<void> {
  const entry = {
    timestamp: new Date().toISOString(),
    level,
    event,
    ...data,
  };

  console.log(JSON.stringify(entry));
}

async function emitMetric(
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

const handlers = new Map<string, ExecutorHandler>();

function registerHandler(action: string, handler: ExecutorHandler): void {
  handlers.set(action, handler);
}

/**
 * Handler exemplo: ping
 */
registerHandler("system.ping", async (ctx) => {
  return {
    ok: true,
    taskId: ctx.taskId,
    requestId: ctx.requestId,
    timestamp: new Date().toISOString(),
  };
});

/**
 * Handler exemplo: echo seguro
 */
registerHandler("system.echo", async (ctx) => {
  return {
    payload: ctx.payload,
    metadata: ctx.metadata,
  };
});

/**
 * Handler exemplo: processamento assíncrono simulado
 */
registerHandler("job.process", async (ctx) => {
  const steps = Number(ctx.payload.steps ?? 3);

  if (steps < 1 || steps > 20) {
    throw new ExecutorError(
      "INVALID_STEPS",
      "steps precisa estar entre 1 e 20.",
      400,
    );
  }

  for (let i = 0; i < steps; i++) {
    if (ctx.signal.aborted) {
      throw new ExecutorError(
        "EXECUTION_ABORTED",
        "Execução abortada.",
        499,
      );
    }

    await sleep(100);
  }

  return {
    processed: true,
    steps,
  };
});

async function executeHandler(
  requestId: string,
  normalized: Required<ExecutorRequest>,
): Promise<ExecutorResponse> {
  const startedAt = now();
  const handler = handlers.get(normalized.action);

  if (!handler) {
    throw new ExecutorError(
      "ACTION_NOT_FOUND",
      `Nenhum handler registrado para action: ${normalized.action}`,
      404,
    );
  }

  await auditLog("info", "executor.started", {
    requestId,
    taskId: normalized.taskId,
    action: normalized.action,
    priority: normalized.priority,
  });

  let lastError: unknown;

  for (let attempt = 0; attempt <= normalized.retries; attempt++) {
    try {
      const result = await withTimeout(normalized.timeoutMs, async (signal) => {
        const ctx: ExecutionContext = {
          requestId,
          taskId: normalized.taskId,
          action: normalized.action,
          startedAt,
          deadlineAt: startedAt + normalized.timeoutMs,
          attempt,
          maxRetries: normalized.retries,
          signal,
          payload: normalized.payload,
          metadata: normalized.metadata,
        };

        return await handler(ctx);
      });

      const response: ExecutorResponse = {
        requestId,
        taskId: normalized.taskId,
        status: "success",
        action: normalized.action,
        durationMs: now() - startedAt,
        result,
      };

      await auditLog("info", "executor.success", {
        requestId,
        taskId: normalized.taskId,
        action: normalized.action,
        durationMs: response.durationMs,
        attempt,
      });

      await emitMetric("executor.success", 1, {
        action: normalized.action,
      });

      await emitMetric("executor.duration_ms", response.durationMs, {
        action: normalized.action,
      });

      return response;
    } catch (error) {
      lastError = error;

      const isLastAttempt = attempt >= normalized.retries;

      await auditLog(isLastAttempt ? "error" : "warn", "executor.retry", {
        requestId,
        taskId: normalized.taskId,
        action: normalized.action,
        attempt,
        maxRetries: normalized.retries,
        error: error instanceof Error ? error.message : String(error),
      });

      if (!isLastAttempt) {
        await sleep(backoffDelay(attempt));
      }
    }
  }

  const durationMs = now() - startedAt;
  const error = normalizeError(lastError);

  await emitMetric("executor.failed", 1, {
    action: normalized.action,
    code: error.code,
  });

  return {
    requestId,
    taskId: normalized.taskId,
    status: error.code === "EXECUTION_TIMEOUT" ? "timeout" : "failed",
    action: normalized.action,
    durationMs,
    error: {
      code: error.code,
      message: error.message,
      details: error.details,
    },
  };
}

function normalizeError(error: unknown): ExecutorError {
  if (error instanceof ExecutorError) {
    return error;
  }

  if (error instanceof Error) {
    return new ExecutorError(
      "INTERNAL_ERROR",
      error.message,
      500,
    );
  }

  return new ExecutorError(
    "UNKNOWN_ERROR",
    "Erro desconhecido durante a execução.",
    500,
    { raw: String(error) },
  );
}

async function handleExecutor(req: Request): Promise<Response> {
  const globalStartedAt = now();
  const requestId = req.headers.get("x-request-id") || createRequestId();

  try {
    if (req.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(),
      });
    }

    if (req.method !== "POST") {
      throw new ExecutorError(
        "METHOD_NOT_ALLOWED",
        "Use POST para executar tarefas.",
        405,
      );
    }

    enforceRateLimit(req);
    await authenticate(req);

    const body = await readJsonBody(req);
    const normalized = normalizeExecutorRequest(body, req);

    if (normalized.idempotencyKey) {
      const cached = idempotencyStore.get(normalized.idempotencyKey);

      if (cached) {
        return jsonResponse({
          ...cached,
          status: cached.status,
          idempotentReplay: true,
        });
      }
    }

    const result = await executeHandler(requestId, normalized);

    if (normalized.idempotencyKey) {
      idempotencyStore.set(normalized.idempotencyKey, result);
    }

    const httpStatus = result.status === "success" ? 200 : 500;

    return jsonResponse(result, httpStatus, {
      "x-request-id": requestId,
      "x-duration-ms": String(now() - globalStartedAt),
    });
  } catch (error) {
    const normalized = normalizeError(error);

    await auditLog("error", "executor.rejected", {
      requestId,
      code: normalized.code,
      message: normalized.message,
      status: normalized.status,
    });

    return jsonResponse(
      {
        requestId,
        taskId: requestId,
        status: normalized.status === 429 ? "rate_limited" : "rejected",
        action: "unknown",
        durationMs: now() - globalStartedAt,
        error: {
          code: normalized.code,
          message: normalized.message,
          details: normalized.details,
        },
      },
      normalized.status,
      {
        "x-request-id": requestId,
      },
    );
  }
}

serve(handleExecutor);