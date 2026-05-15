// edge_functions/fraud_realtime.ts
// Enterprise Fraud Realtime Detection
// Runtime: Deno / Supabase Edge Functions

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";

type RiskLevel = "low" | "medium" | "high" | "critical";
type Decision = "approve" | "review" | "challenge" | "block";

type JsonMap = Record<string, unknown>;

type FraudEvent = {
  eventId?: string;
  tenantId: string;
  userId?: string;
  accountId?: string;
  transactionId?: string;
  sessionId?: string;

  eventType:
    | "login"
    | "payment"
    | "transfer"
    | "withdrawal"
    | "signup"
    | "password_reset"
    | "profile_update"
    | "device_change";

  amount?: number;
  currency?: string;

  ip?: string;
  country?: string;
  city?: string;

  deviceId?: string;
  userAgent?: string;

  email?: string;
  phone?: string;

  metadata?: JsonMap;
  occurredAt?: string;
};

type FraudSignal = {
  code: string;
  description: string;
  score: number;
  severity: RiskLevel;
  evidence?: JsonMap;
};

type FraudResponse = {
  requestId: string;
  eventId: string;
  tenantId: string;
  score: number;
  riskLevel: RiskLevel;
  decision: Decision;
  signals: FraudSignal[];
  durationMs: number;
  modelVersion: string;
};

const CONFIG = {
  modelVersion: "fraud-realtime-v1.0.0",
  maxBodyBytes: 512_000,

  thresholds: {
    review: 35,
    challenge: 60,
    block: 80,
  },

  velocityWindowMs: 5 * 60 * 1000,
  velocityMaxEvents: 8,
  velocityHighAmountMaxEvents: 3,

  highAmountThreshold: 1_000,
  criticalAmountThreshold: 5_000,

  suspiciousCountries: new Set(["KP", "IR", "SY"]),
  blockedEmails: new Set<string>(),
  blockedIps: new Set<string>(),
  blockedDeviceIds: new Set<string>(),
};

const velocityStore = new Map<string, number[]>();
const idempotencyStore = new Map<string, FraudResponse>();

class FraudError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 500,
    public details?: unknown,
  ) {
    super(message);
    this.name = "FraudError";
  }
}

function corsHeaders(): HeadersInit {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST, OPTIONS",
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

function now(): number {
  return Date.now();
}

function requestId(): string {
  return crypto.randomUUID();
}

async function audit(
  level: "info" | "warn" | "error",
  event: string,
  data: JsonMap,
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

async function authenticate(req: Request): Promise<void> {
  const expected = Deno.env.get("FRAUD_REALTIME_API_KEY");

  if (!expected) return;

  const token = (req.headers.get("authorization") ?? "")
    .replace(/^Bearer\s+/i, "")
    .trim();

  if (token !== expected) {
    throw new FraudError("UNAUTHORIZED", "Credencial inválida.", 401);
  }
}

async function readEvent(req: Request): Promise<FraudEvent> {
  const size = Number(req.headers.get("content-length") ?? "0");

  if (size > CONFIG.maxBodyBytes) {
    throw new FraudError("PAYLOAD_TOO_LARGE", "Payload muito grande.", 413);
  }

  try {
    return await req.json();
  } catch {
    throw new FraudError("INVALID_JSON", "JSON inválido.", 400);
  }
}

function validateEvent(event: FraudEvent): void {
  if (!event.tenantId) {
    throw new FraudError("TENANT_REQUIRED", "tenantId é obrigatório.", 400);
  }

  if (!event.eventType) {
    throw new FraudError("EVENT_TYPE_REQUIRED", "eventType é obrigatório.", 400);
  }

  if (
    event.amount !== undefined &&
    (!Number.isFinite(event.amount) || event.amount < 0)
  ) {
    throw new FraudError("INVALID_AMOUNT", "amount inválido.", 400);
  }
}

function normalizeKey(value?: string): string {
  return value?.trim().toLowerCase() || "unknown";
}

function addVelocityEvent(key: string, timestamp: number): number {
  const windowStart = timestamp - CONFIG.velocityWindowMs;
  const events = velocityStore.get(key) ?? [];

  const freshEvents = events.filter((item) => item >= windowStart);
  freshEvents.push(timestamp);

  velocityStore.set(key, freshEvents);

  return freshEvents.length;
}

function addSignal(
  signals: FraudSignal[],
  code: string,
  description: string,
  score: number,
  severity: RiskLevel,
  evidence?: JsonMap,
): void {
  signals.push({
    code,
    description,
    score,
    severity,
    evidence,
  });
}

function evaluateBlacklist(event: FraudEvent, signals: FraudSignal[]): void {
  const ip = normalizeKey(event.ip);
  const email = normalizeKey(event.email);
  const deviceId = normalizeKey(event.deviceId);

  if (CONFIG.blockedIps.has(ip)) {
    addSignal(signals, "BLOCKED_IP", "IP bloqueado.", 90, "critical", { ip });
  }

  if (CONFIG.blockedEmails.has(email)) {
    addSignal(signals, "BLOCKED_EMAIL", "E-mail bloqueado.", 85, "critical", {
      email,
    });
  }

  if (CONFIG.blockedDeviceIds.has(deviceId)) {
    addSignal(
      signals,
      "BLOCKED_DEVICE",
      "Dispositivo bloqueado.",
      85,
      "critical",
      { deviceId },
    );
  }
}

function evaluateAmount(event: FraudEvent, signals: FraudSignal[]): void {
  if (!event.amount) return;

  if (event.amount >= CONFIG.criticalAmountThreshold) {
    addSignal(
      signals,
      "CRITICAL_AMOUNT",
      "Valor extremamente alto.",
      35,
      "high",
      {
        amount: event.amount,
        threshold: CONFIG.criticalAmountThreshold,
      },
    );
    return;
  }

  if (event.amount >= CONFIG.highAmountThreshold) {
    addSignal(
      signals,
      "HIGH_AMOUNT",
      "Valor acima do padrão esperado.",
      20,
      "medium",
      {
        amount: event.amount,
        threshold: CONFIG.highAmountThreshold,
      },
    );
  }
}

function evaluateGeo(event: FraudEvent, signals: FraudSignal[]): void {
  const country = event.country?.toUpperCase();

  if (!country) return;

  if (CONFIG.suspiciousCountries.has(country)) {
    addSignal(
      signals,
      "SUSPICIOUS_COUNTRY",
      "País classificado como alto risco.",
      30,
      "high",
      { country },
    );
  }
}

function evaluateIdentity(event: FraudEvent, signals: FraudSignal[]): void {
  if (event.eventType === "signup" && !event.email && !event.phone) {
    addSignal(
      signals,
      "WEAK_IDENTITY",
      "Cadastro sem e-mail ou telefone.",
      25,
      "medium",
    );
  }

  if (event.eventType === "password_reset" && !event.deviceId) {
    addSignal(
      signals,
      "PASSWORD_RESET_UNKNOWN_DEVICE",
      "Reset de senha sem deviceId.",
      30,
      "high",
    );
  }

  if (event.eventType === "device_change") {
    addSignal(
      signals,
      "DEVICE_CHANGE",
      "Alteração de dispositivo detectada.",
      20,
      "medium",
    );
  }
}

function evaluateVelocity(event: FraudEvent, signals: FraudSignal[]): void {
  const timestamp = now();

  const tenant = normalizeKey(event.tenantId);
  const user = normalizeKey(event.userId || event.accountId || event.email);
  const ip = normalizeKey(event.ip);
  const device = normalizeKey(event.deviceId);

  const userKey = `${tenant}:user:${user}:${event.eventType}`;
  const ipKey = `${tenant}:ip:${ip}:${event.eventType}`;
  const deviceKey = `${tenant}:device:${device}:${event.eventType}`;

  const userCount = addVelocityEvent(userKey, timestamp);
  const ipCount = addVelocityEvent(ipKey, timestamp);
  const deviceCount = addVelocityEvent(deviceKey, timestamp);

  if (userCount > CONFIG.velocityMaxEvents) {
    addSignal(
      signals,
      "USER_VELOCITY",
      "Muitos eventos do mesmo usuário em curto período.",
      30,
      "high",
      { userCount },
    );
  }

  if (ipCount > CONFIG.velocityMaxEvents * 2) {
    addSignal(
      signals,
      "IP_VELOCITY",
      "Muitos eventos do mesmo IP em curto período.",
      25,
      "medium",
      { ipCount },
    );
  }

  if (deviceCount > CONFIG.velocityMaxEvents) {
    addSignal(
      signals,
      "DEVICE_VELOCITY",
      "Muitos eventos do mesmo dispositivo em curto período.",
      25,
      "medium",
      { deviceCount },
    );
  }

  if (
    event.amount &&
    event.amount >= CONFIG.highAmountThreshold &&
    userCount > CONFIG.velocityHighAmountMaxEvents
  ) {
    addSignal(
      signals,
      "HIGH_AMOUNT_VELOCITY",
      "Múltiplas transações de alto valor em curto período.",
      40,
      "critical",
      {
        amount: event.amount,
        userCount,
      },
    );
  }
}

function evaluateDevice(event: FraudEvent, signals: FraudSignal[]): void {
  const ua = event.userAgent?.toLowerCase() ?? "";

  if (!event.deviceId) {
    addSignal(
      signals,
      "MISSING_DEVICE_ID",
      "Evento sem identificação de dispositivo.",
      15,
      "low",
    );
  }

  if (ua.includes("curl") || ua.includes("python") || ua.includes("bot")) {
    addSignal(
      signals,
      "AUTOMATED_CLIENT",
      "Cliente automatizado detectado.",
      35,
      "high",
      { userAgent: event.userAgent },
    );
  }
}

function calculateScore(signals: FraudSignal[]): number {
  const rawScore = signals.reduce((sum, signal) => sum + signal.score, 0);
  return Math.min(100, Math.max(0, Math.round(rawScore)));
}

function riskLevel(score: number): RiskLevel {
  if (score >= 85) return "critical";
  if (score >= 60) return "high";
  if (score >= 35) return "medium";
  return "low";
}

function decision(score: number): Decision {
  if (score >= CONFIG.thresholds.block) return "block";
  if (score >= CONFIG.thresholds.challenge) return "challenge";
  if (score >= CONFIG.thresholds.review) return "review";
  return "approve";
}

function evaluateFraud(event: FraudEvent): {
  score: number;
  riskLevel: RiskLevel;
  decision: Decision;
  signals: FraudSignal[];
} {
  const signals: FraudSignal[] = [];

  evaluateBlacklist(event, signals);
  evaluateAmount(event, signals);
  evaluateGeo(event, signals);
  evaluateIdentity(event, signals);
  evaluateVelocity(event, signals);
  evaluateDevice(event, signals);

  const score = calculateScore(signals);

  return {
    score,
    riskLevel: riskLevel(score),
    decision: decision(score),
    signals,
  };
}

function normalizeError(error: unknown): FraudError {
  if (error instanceof FraudError) return error;

  if (error instanceof Error) {
    return new FraudError("INTERNAL_ERROR", error.message, 500);
  }

  return new FraudError("UNKNOWN_ERROR", "Erro desconhecido.", 500);
}

async function handler(req: Request): Promise<Response> {
  const startedAt = now();
  const reqId = req.headers.get("x-request-id") || requestId();

  try {
    if (req.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(),
      });
    }

    if (req.method !== "POST") {
      throw new FraudError("METHOD_NOT_ALLOWED", "Use POST.", 405);
    }

    await authenticate(req);

    const idemKey = req.headers.get("x-idempotency-key");

    if (idemKey && idempotencyStore.has(idemKey)) {
      return json({
        ...idempotencyStore.get(idemKey),
        idempotentReplay: true,
      });
    }

    const event = await readEvent(req);
    validateEvent(event);

    const eventId = event.eventId || crypto.randomUUID();

    await audit("info", "fraud.event.received", {
      requestId: reqId,
      eventId,
      tenantId: event.tenantId,
      eventType: event.eventType,
    });

    const evaluation = evaluateFraud(event);

    const response: FraudResponse = {
      requestId: reqId,
      eventId,
      tenantId: event.tenantId,
      score: evaluation.score,
      riskLevel: evaluation.riskLevel,
      decision: evaluation.decision,
      signals: evaluation.signals,
      durationMs: now() - startedAt,
      modelVersion: CONFIG.modelVersion,
    };

    if (idemKey) {
      idempotencyStore.set(idemKey, response);
    }

    await audit("info", "fraud.event.evaluated", {
      requestId: reqId,
      eventId,
      tenantId: event.tenantId,
      score: response.score,
      riskLevel: response.riskLevel,
      decision: response.decision,
      durationMs: response.durationMs,
    });

    await metric("fraud.realtime.score", response.score, {
      tenantId: event.tenantId,
      eventType: event.eventType,
      decision: response.decision,
    });

    return json(response, 200, {
      "x-request-id": reqId,
      "x-risk-level": response.riskLevel,
      "x-fraud-decision": response.decision,
    });
  } catch (error) {
    const err = normalizeError(error);

    await audit("error", "fraud.event.rejected", {
      requestId: reqId,
      code: err.code,
      message: err.message,
      status: err.status,
    });

    return json(
      {
        requestId: reqId,
        status: "error",
        error: {
          code: err.code,
          message: err.message,
          details: err.details,
        },
        durationMs: now() - startedAt,
      },
      err.status,
      {
        "x-request-id": reqId,
      },
    );
  }
}

serve(handler);