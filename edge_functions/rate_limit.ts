
// edge_functions/rate_limit.ts
// Enterprise Rate Limiting Module for Deno / Supabase Edge Functions

export type RateLimitAlgorithm =
  | "fixed_window"
  | "sliding_window"
  | "token_bucket";

export type RateLimitKeyInput = {
  tenantId?: string;
  userId?: string;
  apiKey?: string;
  ip?: string;
  route?: string;
  method?: string;
};

export type RateLimitConfig = {
  enabled: boolean;
  algorithm: RateLimitAlgorithm;
  windowMs: number;
  maxRequests: number;
  burst?: number;
  refillRatePerSecond?: number;
  namespace?: string;
  blockOnMissingIdentity?: boolean;
};

export type RateLimitResult = {
  allowed: boolean;
  key: string;
  limit: number;
  remaining: number;
  resetAt: number;
  retryAfterMs: number;
  algorithm: RateLimitAlgorithm;
  reason?: string;
};

type FixedWindowBucket = {
  count: number;
  resetAt: number;
};

type SlidingWindowBucket = {
  timestamps: number[];
};

type TokenBucket = {
  tokens: number;
  updatedAt: number;
};

const fixedWindowStore = new Map<string, FixedWindowBucket>();
const slidingWindowStore = new Map<string, SlidingWindowBucket>();
const tokenBucketStore = new Map<string, TokenBucket>();

export class RateLimitError extends Error {
  constructor(
    public result: RateLimitResult,
    message = "Rate limit exceeded",
  ) {
    super(message);
    this.name = "RateLimitError";
  }
}

export const DEFAULT_RATE_LIMIT_CONFIG: RateLimitConfig = {
  enabled: true,
  algorithm: "sliding_window",
  windowMs: 60_000,
  maxRequests: 120,
  burst: 30,
  refillRatePerSecond: 2,
  namespace: "edge",
  blockOnMissingIdentity: false,
};

function now(): number {
  return Date.now();
}

function normalize(value?: string): string {
  return value?.trim().toLowerCase() || "";
}

export function getClientIp(req: Request): string {
  const forwarded = req.headers.get("x-forwarded-for");
  const realIp = forwarded?.split(",")[0]?.trim();

  return (
    realIp ||
    req.headers.get("cf-connecting-ip") ||
    req.headers.get("x-real-ip") ||
    "unknown"
  );
}

export function buildRateLimitKey(input: RateLimitKeyInput): string {
  const tenant = normalize(input.tenantId) || "global";
  const route = normalize(input.route) || "unknown-route";
  const method = normalize(input.method) || "unknown-method";

  const identity =
    normalize(input.userId) ||
    normalize(input.apiKey) ||
    normalize(input.ip) ||
    "anonymous";

  return `${tenant}:${route}:${method}:${identity}`;
}

export function parseBearerToken(req: Request): string {
  return (req.headers.get("authorization") ?? "")
    .replace(/^Bearer\s+/i, "")
    .trim();
}

export function createRateLimitInputFromRequest(
  req: Request,
  options: {
    tenantId?: string;
    userId?: string;
    route?: string;
  } = {},
): RateLimitKeyInput {
  const url = new URL(req.url);

  return {
    tenantId:
      options.tenantId ||
      req.headers.get("x-tenant-id") ||
      undefined,
    userId:
      options.userId ||
      req.headers.get("x-user-id") ||
      undefined,
    apiKey: parseBearerToken(req) || undefined,
    ip: getClientIp(req),
    route: options.route || url.pathname,
    method: req.method,
  };
}

function fixedWindowLimit(
  key: string,
  config: RateLimitConfig,
): RateLimitResult {
  const current = now();
  const bucket = fixedWindowStore.get(key);

  if (!bucket || bucket.resetAt <= current) {
    const resetAt = current + config.windowMs;

    fixedWindowStore.set(key, {
      count: 1,
      resetAt,
    });

    return {
      allowed: true,
      key,
      limit: config.maxRequests,
      remaining: config.maxRequests - 1,
      resetAt,
      retryAfterMs: 0,
      algorithm: "fixed_window",
    };
  }

  bucket.count++;

  const remaining = Math.max(config.maxRequests - bucket.count, 0);
  const allowed = bucket.count <= config.maxRequests;

  return {
    allowed,
    key,
    limit: config.maxRequests,
    remaining,
    resetAt: bucket.resetAt,
    retryAfterMs: allowed ? 0 : bucket.resetAt - current,
    algorithm: "fixed_window",
    reason: allowed ? undefined : "fixed_window_limit_exceeded",
  };
}

function slidingWindowLimit(
  key: string,
  config: RateLimitConfig,
): RateLimitResult {
  const current = now();
  const windowStart = current - config.windowMs;

  const bucket = slidingWindowStore.get(key) ?? { timestamps: [] };

  bucket.timestamps = bucket.timestamps.filter((time) => time > windowStart);

  const allowed = bucket.timestamps.length < config.maxRequests;

  if (allowed) {
    bucket.timestamps.push(current);
  }

  slidingWindowStore.set(key, bucket);

  const oldest = bucket.timestamps[0] ?? current;
  const resetAt = oldest + config.windowMs;

  return {
    allowed,
    key,
    limit: config.maxRequests,
    remaining: Math.max(config.maxRequests - bucket.timestamps.length, 0),
    resetAt,
    retryAfterMs: allowed ? 0 : Math.max(resetAt - current, 0),
    algorithm: "sliding_window",
    reason: allowed ? undefined : "sliding_window_limit_exceeded",
  };
}

function tokenBucketLimit(
  key: string,
  config: RateLimitConfig,
): RateLimitResult {
  const current = now();

  const capacity = config.burst ?? config.maxRequests;
  const refillRate = config.refillRatePerSecond ?? config.maxRequests / 60;

  const bucket = tokenBucketStore.get(key) ?? {
    tokens: capacity,
    updatedAt: current,
  };

  const elapsedSeconds = Math.max((current - bucket.updatedAt) / 1000, 0);
  bucket.tokens = Math.min(
    capacity,
    bucket.tokens + elapsedSeconds * refillRate,
  );
  bucket.updatedAt = current;

  const allowed = bucket.tokens >= 1;

  if (allowed) {
    bucket.tokens -= 1;
  }

  tokenBucketStore.set(key, bucket);

  const missingTokens = Math.max(1 - bucket.tokens, 0);
  const retryAfterMs = allowed
    ? 0
    : Math.ceil((missingTokens / refillRate) * 1000);

  return {
    allowed,
    key,
    limit: capacity,
    remaining: Math.floor(Math.max(bucket.tokens, 0)),
    resetAt: current + retryAfterMs,
    retryAfterMs,
    algorithm: "token_bucket",
    reason: allowed ? undefined : "token_bucket_empty",
  };
}

export function checkRateLimit(
  input: RateLimitKeyInput,
  partialConfig: Partial<RateLimitConfig> = {},
): RateLimitResult {
  const config: RateLimitConfig = {
    ...DEFAULT_RATE_LIMIT_CONFIG,
    ...partialConfig,
  };

  const key = `${config.namespace}:${buildRateLimitKey(input)}`;

  if (!config.enabled) {
    return {
      allowed: true,
      key,
      limit: config.maxRequests,
      remaining: config.maxRequests,
      resetAt: now() + config.windowMs,
      retryAfterMs: 0,
      algorithm: config.algorithm,
      reason: "rate_limit_disabled",
    };
  }

  if (config.blockOnMissingIdentity) {
    const hasIdentity = input.userId || input.apiKey || input.ip;

    if (!hasIdentity) {
      return {
        allowed: false,
        key,
        limit: config.maxRequests,
        remaining: 0,
        resetAt: now() + config.windowMs,
        retryAfterMs: config.windowMs,
        algorithm: config.algorithm,
        reason: "missing_identity",
      };
    }
  }

  switch (config.algorithm) {
    case "fixed_window":
      return fixedWindowLimit(key, config);

    case "sliding_window":
      return slidingWindowLimit(key, config);

    case "token_bucket":
      return tokenBucketLimit(key, config);

    default:
      return slidingWindowLimit(key, config);
  }
}

export function rateLimitHeaders(result: RateLimitResult): HeadersInit {
  return {
    "x-ratelimit-key": result.key,
    "x-ratelimit-limit": String(result.limit),
    "x-ratelimit-remaining": String(result.remaining),
    "x-ratelimit-reset": String(Math.ceil(result.resetAt / 1000)),
    "retry-after": String(Math.ceil(result.retryAfterMs / 1000)),
  };
}

export function rateLimitJsonResponse(result: RateLimitResult): Response {
  return new Response(
    JSON.stringify(
      {
        status: "error",
        error: {
          code: "RATE_LIMITED",
          message: "Limite de requisições excedido.",
          reason: result.reason,
        },
        rateLimit: {
          algorithm: result.algorithm,
          limit: result.limit,
          remaining: result.remaining,
          resetAt: new Date(result.resetAt).toISOString(),
          retryAfterMs: result.retryAfterMs,
        },
      },
      null,
      2,
    ),
    {
      status: 429,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        ...rateLimitHeaders(result),
      },
    },
  );
}

export function enforceRateLimit(
  input: RateLimitKeyInput,
  config?: Partial<RateLimitConfig>,
): RateLimitResult {
  const result = checkRateLimit(input, config);

  if (!result.allowed) {
    throw new RateLimitError(result);
  }

  return result;
}

export async function withRateLimit(
  req: Request,
  handler: (
    req: Request,
    context: {
      rateLimit: RateLimitResult;
    },
  ) => Promise<Response> | Response,
  config?: Partial<RateLimitConfig>,
): Promise<Response> {
  const input = createRateLimitInputFromRequest(req);
  const result = checkRateLimit(input, config);

  if (!result.allowed) {
    return rateLimitJsonResponse(result);
  }

  const response = await handler(req, {
    rateLimit: result,
  });

  const headers = new Headers(response.headers);

  for (const [key, value] of Object.entries(rateLimitHeaders(result))) {
    headers.set(key, value);
  }

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export function cleanupRateLimitStores(): void {
  const current = now();

  for (const [key, bucket] of fixedWindowStore.entries()) {
    if (bucket.resetAt <= current) {
      fixedWindowStore.delete(key);
    }
  }

  for (const [key, bucket] of slidingWindowStore.entries()) {
    const active = bucket.timestamps.filter(
      (timestamp) => timestamp > current - DEFAULT_RATE_LIMIT_CONFIG.windowMs,
    );

    if (active.length === 0) {
      slidingWindowStore.delete(key);
    } else {
      bucket.timestamps = active;
    }
  }

  for (const [key, bucket] of tokenBucketStore.entries()) {
    if (current - bucket.updatedAt > 10 * 60_000) {
      tokenBucketStore.delete(key);
    }
  }
}

export function getRateLimitSnapshot(): {
  fixedWindowKeys: number;
  slidingWindowKeys: number;
  tokenBucketKeys: number;
} {
  return {
    fixedWindowKeys: fixedWindowStore.size,
    slidingWindowKeys: slidingWindowStore.size,
    tokenBucketKeys: tokenBucketStore.size,
  };
}