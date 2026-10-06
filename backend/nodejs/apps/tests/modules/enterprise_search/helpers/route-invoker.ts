import type { Router } from 'express'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { FakeSSEResponse } from '../controller/chat-test-harness'

export interface InvokeRouteOptions {
  method: string
  /** Path inside the router, with an optional query string. */
  url: string
  user?: Record<string, unknown>
  body?: unknown
  headers?: Record<string, string>
  /** Extra request fields, for example a `tokenPayload` on internal routes. */
  request?: Record<string, unknown>
}

export interface RouteOutcome {
  status: number | undefined
  /** The JSON the client would receive, including the error envelope. */
  body: unknown
  /** What the route passed to `next(err)`, before the error middleware turned it into `body`. */
  error: (Error & { statusCode?: number; code?: string }) | undefined
  res: FakeSSEResponse
}

type Handle = (req: unknown, res: unknown, next: (err?: unknown) => void) => void

/**
 * Sends one request through `router.handle` as Express would: real route
 * matching, middleware and handler, a `FakeSSEResponse`, and the production
 * error middleware. There is no supertest in this package. Resolves when the
 * response ends or the router hands back an error (or falls through).
 */
export function invokeRoute(router: Router, options: InvokeRouteOptions): Promise<RouteOutcome> {
  return new Promise((resolve) => {
    const res = new FakeSSEResponse()
    let settled = false
    const finish = (error?: unknown): void => {
      if (settled) return
      settled = true
      resolve({
        status: res.statusCode,
        body: res.jsonBody,
        error: error as RouteOutcome['error'],
        res,
      })
    }
    void res.ended.then(() => finish())

    const [path, search = ''] = options.url.split('?')
    const query = Object.fromEntries(new URLSearchParams(search))
    const request: Record<string, unknown> = {
      method: options.method.toUpperCase(),
      url: options.url,
      originalUrl: options.url,
      baseUrl: '',
      path,
      headers: { authorization: 'Bearer token', ...options.headers },
      body: options.body ?? {},
      query,
      context: { requestId: 'req-route-invoker' },
      on: () => request,
      ...(options.user !== undefined && { user: options.user }),
      ...options.request,
    }

    const done = (err?: unknown): void => {
      if (err === undefined) {
        finish()
        return
      }
      const logger = Logger.getInstance() as unknown as Record<'error' | 'warn', () => void>
      const saved = { error: logger.error, warn: logger.warn }
      logger.error = logger.warn = () => undefined
      try {
        ErrorMiddleware.handleError()(err as Error, request as never, res as never, () => undefined)
      } finally {
        Object.assign(logger, saved)
      }
      finish(err)
    }
    ;(router as unknown as { handle: Handle }).handle(request, res, done)
  })
}
