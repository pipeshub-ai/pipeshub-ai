import express, { NextFunction, Request, RequestHandler, Response } from 'express'
import http from 'http'
import { AddressInfo } from 'net'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Env } from './conversation-world'

export interface TestUser {
  userId: string
  orgId: string
  /** Present: the caller is an OAuth app holding exactly these scopes. */
  scopes?: string[]
}

/** Reads the caller from `x-test-user`, so one server can answer as several people. */
export const headerAuth: RequestHandler = (req: Request, _res: Response, next: NextFunction) => {
  const raw = req.headers['x-test-user']
  if (typeof raw === 'string') {
    const user = JSON.parse(raw) as TestUser
    ;(req as unknown as { user: Record<string, unknown> }).user = {
      userId: user.userId,
      orgId: user.orgId,
      email: `${user.userId}@example.com`,
      ...(user.scopes && { isOAuth: true, oauthScopes: user.scopes }),
    }
  }
  next()
}

export interface Served {
  call(method: string, path: string, user: TestUser, body?: unknown, headers?: Record<string, string>): Promise<{ status: number; body: any; headers: Headers }>
  close(): Promise<void>
}

/** Both routers on a real HTTP server with the production error middleware; `buildRouters` must have been given `headerAuth`. */
export async function serve(env: Env): Promise<Served> {
  const app = express()
  app.use(express.json())
  app.use('/api/v1/conversations', env.chat)
  app.use('/api/v1/agents', env.agent)
  app.use(ErrorMiddleware.handleError())
  const server = http.createServer(app)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  return {
    async call(method, path, user, body, headers = {}) {
      const res = await fetch(`${origin}${path}`, {
        method,
        headers: { 'x-test-user': JSON.stringify(user), ...(body !== undefined && { 'content-type': 'application/json' }), ...headers },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const text = await res.text()
      return { status: res.status, body: text ? JSON.parse(text) : undefined, headers: res.headers }
    },
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}
