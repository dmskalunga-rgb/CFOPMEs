// edge_functions/router.ts
// Enterprise Router Module for Deno / Supabase Edge Functions

export type HttpMethod =
  | "GET"
  | "POST"
  | "PUT"
  | "PATCH"
  | "DELETE"
  | "OPTIONS"
  | "HEAD";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type RouteParams = Record<string, string>;

export type RouterContext<TState extends Record<string, unknown> = Record<string, unknown>> = {
  req: Request;
  url: URL;
  requestId: string;
  startedAt: number;
  params: RouteParams;
  query: URLSearchParams;
  state: TState;
};

export type RouteHandler<TState extends Record<string, unknown> = Record<string, unknown>> = (
  ctx: RouterContext<TState>,
) => Promise<Response> | Response;

export type Middleware<TState extends Record<string, unknown> = Record<string, unknown>> = (
  ctx: RouterContext<TState>,
  next: () => Promise<Response>,
) => Promise<Response> | Response;

export type RouteDefinition<TState extends Record<string, unknown> = Record<string, unknown>> = {
  method: HttpMethod;
  path: string;
  handler: RouteHandler<TState>;
  middlewares?: Middleware<TState>[];
  name?: string;
};

type CompiledRoute<TState extends Record<string, unknown>> = RouteDefinition<TState> & {
  regex: RegExp;
  paramNames: string[];
};

export type RouterOptions<TState extends Record<string, unknown> = Record<string, unknown>> = {
  basePath?: string;
  notFoundHandler?: RouteHandler<TState>;
  errorHandler?: (
    error: unknown,
    ctx: RouterContext<TState>,
  ) => Promise<Response> | Response;
  globalMiddlewares?: Middleware<TState>[];
  cors?: {
    enabled: boolean;
    origin?: string;
    methods?: string[];
    headers?: string[];
    credentials?: boolean;
    maxAge?: number;
  };
};

export class RouterError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 500,
    public details?: JsonValue,
  ) {
    super(message);
    this.name = "RouterError";
  }
}

function now(): number {
  return Date.now();
}

function uuid(): string {
  return crypto.randomUUID();
}

export function jsonResponse(
  body: JsonValue,
  status = 200,
  headers: HeadersInit = {},
): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers,
    },
  });
}

export function textResponse(
  body: string,
  status = 200,
  headers: HeadersInit = {},
): Response {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      ...headers,
    },
  });
}

export function getRequestId(req: Request): string {
  return req.headers.get("x-request-id") || uuid();
}

function normalizePath(path: string): string {
  if (!path.startsWith("/")) path = `/${path}`;
  return path.replace(/\/+$/, "") || "/";
}

function joinPath(basePath: string, path: string): string {
  const base = normalizePath(basePath || "");
  const child = normalizePath(path);

  if (base === "/") return child;
  if (child === "/") return base;

  return `${base}${child}`;
}

function compilePath(path: string): {
  regex: RegExp;
  paramNames: string[];
} {
  const paramNames: string[] = [];

  const pattern = normalizePath(path)
    .split("/")
    .map((part) => {
      if (part.startsWith(":")) {
        paramNames.push(part.slice(1));
        return "([^/]+)";
      }

      if (part === "*") {
        paramNames.push("wildcard");
        return "(.*)";
      }

      return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    })
    .join("/");

  return {
    regex: new RegExp(`^${pattern}$`),
    paramNames,
  };
}

function extractParams(
  route: CompiledRoute<Record<string, unknown>>,
  pathname: string,
): RouteParams | null {
  const match = pathname.match(route.regex);

  if (!match) return null;

  const params: RouteParams = {};

  route.paramNames.forEach((name, index) => {
    params[name] = decodeURIComponent(match[index + 1] ?? "");
  });

  return params;
}

function corsHeaders(options?: RouterOptions["cors"]): HeadersInit {
  if (!options?.enabled) return {};

  return {
    "access-control-allow-origin": options.origin ?? "*",
    "access-control-allow-methods": (
      options.methods ?? ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    ).join(", "),
    "access-control-allow-headers": (
      options.headers ?? [
        "authorization",
        "content-type",
        "x-request-id",
        "x-idempotency-key",
        "x-tenant-id",
        "x-user-id",
      ]
    ).join(", "),
    "access-control-allow-credentials": String(options.credentials ?? false),
    "access-control-max-age": String(options.maxAge ?? 86400),
  };
}

function mergeCors(response: Response, options?: RouterOptions["cors"]): Response {
  const headers = new Headers(response.headers);

  for (const [key, value] of Object.entries(corsHeaders(options))) {
    headers.set(key, value);
  }

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function normalizeError(error: unknown): RouterError {
  if (error instanceof RouterError) return error;

  if (error instanceof Error) {
    return new RouterError("INTERNAL_ERROR", error.message, 500);
  }

  return new RouterError("UNKNOWN_ERROR", "Erro desconhecido.", 500);
}

async function compose<TState extends Record<string, unknown>>(
  ctx: RouterContext<TState>,
  middlewares: Middleware<TState>[],
  handler: RouteHandler<TState>,
): Promise<Response> {
  let index = -1;

  async function dispatch(position: number): Promise<Response> {
    if (position <= index) {
      throw new RouterError(
        "MIDDLEWARE_REENTRY",
        "next() foi chamado múltiplas vezes.",
        500,
      );
    }

    index = position;

    const middleware = middlewares[position];

    if (middleware) {
      return await middleware(ctx, () => dispatch(position + 1));
    }

    return await handler(ctx);
  }

  return await dispatch(0);
}

export class EdgeRouter<TState extends Record<string, unknown> = Record<string, unknown>> {
  private routes: CompiledRoute<TState>[] = [];

  constructor(private options: RouterOptions<TState> = {}) {}

  add(
    method: HttpMethod,
    path: string,
    handler: RouteHandler<TState>,
    middlewares: Middleware<TState>[] = [],
    name?: string,
  ): this {
    const fullPath = joinPath(this.options.basePath ?? "", path);
    const compiled = compilePath(fullPath);

    this.routes.push({
      method,
      path: fullPath,
      handler,
      middlewares,
      name,
      regex: compiled.regex,
      paramNames: compiled.paramNames,
    });

    return this;
  }

  get(path: string, handler: RouteHandler<TState>, middlewares?: Middleware<TState>[]): this {
    return this.add("GET", path, handler, middlewares);
  }

  post(path: string, handler: RouteHandler<TState>, middlewares?: Middleware<TState>[]): this {
    return this.add("POST", path, handler, middlewares);
  }

  put(path: string, handler: RouteHandler<TState>, middlewares?: Middleware<TState>[]): this {
    return this.add("PUT", path, handler, middlewares);
  }

  patch(path: string, handler: RouteHandler<TState>, middlewares?: Middleware<TState>[]): this {
    return this.add("PATCH", path, handler, middlewares);
  }

  delete(path: string, handler: RouteHandler<TState>, middlewares?: Middleware<TState>[]): this {
    return this.add("DELETE", path, handler, middlewares);
  }

  options(path: string, handler: RouteHandler<TState>, middlewares?: Middleware<TState>[]): this {
    return this.add("OPTIONS", path, handler, middlewares);
  }

  use(middleware: Middleware<TState>): this {
    this.options.globalMiddlewares = [
      ...(this.options.globalMiddlewares ?? []),
      middleware,
    ];

    return this;
  }

  listRoutes(): Array<{
    method: HttpMethod;
    path: string;
    name?: string;
  }> {
    return this.routes.map((route) => ({
      method: route.method,
      path: route.path,
      name: route.name,
    }));
  }

  handler(): (req: Request) => Promise<Response> {
    return async (req: Request): Promise<Response> => {
      const startedAt = now();
      const requestId = getRequestId(req);
      const url = new URL(req.url);
      const pathname = normalizePath(url.pathname);

      const baseContext: RouterContext<TState> = {
        req,
        url,
        requestId,
        startedAt,
        params: {},
        query: url.searchParams,
        state: {} as TState,
      };

      try {
        if (req.method === "OPTIONS" && this.options.cors?.enabled) {
          return new Response(null, {
            status: 204,
            headers: corsHeaders(this.options.cors),
          });
        }

        const matched = this.routes
          .filter((route) => route.method === req.method)
          .map((route) => ({
            route,
            params: extractParams(
              route as unknown as CompiledRoute<Record<string, unknown>>,
              pathname,
            ),
          }))
          .find((item) => item.params !== null);

        if (!matched) {
          const methodExists = this.routes.some((route) =>
            extractParams(
              route as unknown as CompiledRoute<Record<string, unknown>>,
              pathname,
            ) !== null
          );

          if (methodExists) {
            throw new RouterError(
              "METHOD_NOT_ALLOWED",
              "Método HTTP não permitido para esta rota.",
              405,
            );
          }

          const notFound = this.options.notFoundHandler ??
            (() =>
              jsonResponse({
                requestId,
                status: "error",
                error: {
                  code: "ROUTE_NOT_FOUND",
                  message: "Rota não encontrada.",
                },
                durationMs: now() - startedAt,
              }, 404));

          const response = await notFound(baseContext);

          return mergeCors(response, this.options.cors);
        }

        const ctx: RouterContext<TState> = {
          ...baseContext,
          params: matched.params ?? {},
        };

        const middlewares = [
          ...(this.options.globalMiddlewares ?? []),
          ...(matched.route.middlewares ?? []),
        ];

        const response = await compose(
          ctx,
          middlewares,
          matched.route.handler,
        );

        const headers = new Headers(response.headers);
        headers.set("x-request-id", requestId);
        headers.set("x-duration-ms", String(now() - startedAt));

        const finalResponse = new Response(response.body, {
          status: response.status,
          statusText: response.statusText,
          headers,
        });

        return mergeCors(finalResponse, this.options.cors);
      } catch (error) {
        const err = normalizeError(error);

        if (this.options.errorHandler) {
          const response = await this.options.errorHandler(err, baseContext);
          return mergeCors(response, this.options.cors);
        }

        return mergeCors(
          jsonResponse({
            requestId,
            status: "error",
            error: {
              code: err.code,
              message: err.message,
              details: err.details,
            },
            durationMs: now() - startedAt,
          }, err.status, {
            "x-request-id": requestId,
          }),
          this.options.cors,
        );
      }
    };
  }
}

export const loggerMiddleware: Middleware = async (ctx, next) => {
  const startedAt = now();

  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    level: "info",
    event: "http.request.started",
    requestId: ctx.requestId,
    method: ctx.req.method,
    path: ctx.url.pathname,
  }));

  const response = await next();

  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    level: "info",
    event: "http.request.finished",
    requestId: ctx.requestId,
    method: ctx.req.method,
    path: ctx.url.pathname,
    status: response.status,
    durationMs: now() - startedAt,
  }));

  return response;
};

export const securityHeadersMiddleware: Middleware = async (_ctx, next) => {
  const response = await next();
  const headers = new Headers(response.headers);

  headers.set("x-content-type-options", "nosniff");
  headers.set("x-frame-options", "DENY");
  headers.set("referrer-policy", "no-referrer");
  headers.set("permissions-policy", "camera=(), microphone=(), geolocation=()");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
};

export function requireBearerToken(expectedToken: string): Middleware {
  return async (ctx, next) => {
    if (!expectedToken) return await next();

    const token = (ctx.req.headers.get("authorization") ?? "")
      .replace(/^Bearer\s+/i, "")
      .trim();

    if (token !== expectedToken) {
      throw new RouterError(
        "UNAUTHORIZED",
        "Token inválido ou ausente.",
        401,
      );
    }

    return await next();
  };
}

export function maxBodySizeMiddleware(maxBytes: number): Middleware {
  return async (ctx, next) => {
    const contentLength = Number(ctx.req.headers.get("content-length") ?? "0");

    if (contentLength > maxBytes) {
      throw new RouterError(
        "PAYLOAD_TOO_LARGE",
        "Payload excede o limite permitido.",
        413,
        { maxBytes },
      );
    }

    return await next();
  };
}

export async function readJsonBody<T = unknown>(req: Request): Promise<T> {
  try {
    return await req.json() as T;
  } catch {
    throw new RouterError(
      "INVALID_JSON",
      "Body precisa ser um JSON válido.",
      400,
    );
  }
}