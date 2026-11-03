import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express, { NextFunction, Request, Response, Router } from 'express'
import { Container } from 'inversify'
import {
  INVALID_PATH_SEGMENT_MESSAGE,
  guardPathParams,
  isSafePathSegment,
} from '../../../src/libs/middlewares/safe-path-params.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { BadRequestError } from '../../../src/libs/errors/http.errors'
import { createConnectorRouter } from '../../../src/modules/tokens_manager/routes/connectors.routes'
import { createOAuthRouter } from '../../../src/modules/tokens_manager/routes/oauth.routes'
import { createToolsetsRouter } from '../../../src/modules/toolsets/routes/toolsets_routes'
import { createKnowledgeBaseRouter } from '../../../src/modules/knowledge_base/routes/kb.routes'
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../src/modules/enterprise_search/routes/es.routes'
import { createTeamsRouter } from '../../../src/modules/user_management/routes/teams.routes'
import { createCrawlingManagerRouter } from '../../../src/modules/crawling_manager/routes/cm_routes'
import { createMcpServersRouter } from '../../../src/modules/mcp_servers/routes/mcp_servers.routes'
import { createSkillsRouter } from '../../../src/modules/skills/routes/skills.routes'
import { createConfigurationManagerRouter } from '../../../src/modules/configuration_manager/routes/cm_routes'

describe('safe path params', () => {
  describe('isSafePathSegment', () => {
    for (const value of [
      'SHAREPOINT ONLINE',
      'CONFLUENCE DATA CENTER PERSONAL',
      '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90',
      'knowledgeBase_64b000000000000000000a01',
      'gpt-4.1',
      'frontend-slides',
      'a+b:c@d',
      '...',
      'v1.2',
    ]) {
      it(`accepts ${JSON.stringify(value)}`, () => {
        expect(isSafePathSegment(value)).to.equal(true)
      })
    }

    for (const value of ['', '.', '..', ' .. ', '.. ', ' .', '   ', 'a/b', '../x', 'a\\b', 'a?b', 'a#b', 'a%2Fb', 'a\u0000b', 'a\nb', 'a\u007fb']) {
      it(`refuses ${JSON.stringify(value)}`, () => {
        expect(isSafePathSegment(value)).to.equal(false)
      })
    }

    it('refuses a value that is not a string', () => {
      expect(isSafePathSegment(['a'])).to.equal(false)
      expect(isSafePathSegment(undefined)).to.equal(false)
    })
  })

  describe('guardPathParams on a mounted router', () => {
    let server: http.Server
    let origin: string

    before(async () => {
      const router = Router()
      guardPathParams(router, 'id')
      router.get('/items/:id', (req, res) => {
        res.json({ id: req.params.id })
      })
      router.get('/open/:other', (req, res) => {
        res.json({ other: req.params.other })
      })
      const app = express()
      app.use('/api', router)
      app.use(ErrorMiddleware.handleError())
      server = http.createServer(app)
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
      origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    after(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    })

    const get = (rawPath: string): Promise<{ status: number; body: Record<string, unknown> }> =>
      new Promise((resolve, reject) => {
        // A path option, not a URL string, so `%2E%2E` reaches Express unresolved.
        http
          .get({ host: '127.0.0.1', port: Number(new URL(origin).port), path: rawPath }, (res) => {
            let text = ''
            res.on('data', (c: Buffer) => (text += c.toString('utf8')))
            res.on('end', () => {
              resolve({ status: res.statusCode ?? 0, body: JSON.parse(text) as Record<string, unknown> })
            })
          })
          .on('error', reject)
      })

    it('lets a plain id with spaces through', async () => {
      const r = await get('/api/items/SHAREPOINT%20ONLINE')
      expect(r).to.deep.equal({ status: 200, body: { id: 'SHAREPOINT ONLINE' } })
    })

    it('answers 400 with a plain-language message for a decoded slash', async () => {
      const r = await get('/api/items/..%2Fadmin')
      expect(r.status).to.equal(400)
      expect((r.body.error as { message: string }).message).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
    })

    it('answers 400 for a dot-dot segment sent verbatim', async () => {
      const r = await get('/api/items/%2E%2E')
      expect(r.status).to.equal(400)
    })

    it('leaves params it was not asked to guard alone', async () => {
      const r = await get('/api/open/a%2Fb')
      expect(r).to.deep.equal({ status: 200, body: { other: 'a/b' } })
    })
  })

  describe('every router that pastes path params into a service URL guards them', () => {
    // Stands in for every dependency a router factory resolves; only the param
    // guards registered on the returned router are exercised.
    const anything: unknown = new Proxy(function () {}, {
      get: (_target, prop) => (prop === 'then' ? undefined : anything),
      apply: () => anything,
      construct: () => anything as object,
    })
    const container = { get: () => anything, isBound: () => true } as unknown as Container

    const ROUTERS: Array<{ label: string; create: () => Router; params: string[] }> = [
      {
        label: 'connectors',
        create: () => createConnectorRouter(container, container),
        params: ['connectorId', 'connectorType', 'filterKey', 'recordId'],
      },
      { label: 'oauth configs', create: () => createOAuthRouter(container), params: ['connectorType', 'configId'] },
      {
        label: 'toolsets',
        create: () => createToolsetsRouter(container),
        params: ['toolsetId', 'toolsetType', 'instanceId', 'oauthConfigId', 'agentKey'],
      },
      {
        label: 'knowledge base',
        create: () => createKnowledgeBaseRouter(container),
        params: ['kbId', 'folderId', 'recordId', 'recordGroupId', 'parentType', 'parentId'],
      },
      { label: 'conversations', create: () => createConversationalRouter(container), params: ['recordId'] },
      {
        label: 'agent conversations',
        create: () => createAgentConversationalRouter(container),
        params: ['agentKey', 'recordId', 'provider', 'model_key'],
      },
      { label: 'teams', create: () => createTeamsRouter(container), params: ['teamId'] },
      { label: 'crawling manager', create: () => createCrawlingManagerRouter(container), params: ['connector', 'connectorId'] },
      { label: 'MCP servers', create: () => createMcpServersRouter(container), params: ['typeId', 'instanceId', 'agentKey'] },
      { label: 'skills', create: () => createSkillsRouter(container), params: ['name', 'version', 'candidateId'] },
      { label: 'configuration manager', create: () => createConfigurationManagerRouter(container), params: ['providerId'] },
    ]

    type ParamHandler = (req: Request, res: Response, next: NextFunction, value: unknown) => void
    const outcome = (handlers: ParamHandler[], value: string): unknown => {
      let passed: unknown = 'not called'
      for (const handler of handlers) {
        handler({} as Request, {} as Response, (err?: unknown) => (passed = err), value)
        if (passed !== undefined) break
      }
      return passed
    }

    for (const { label, create, params } of ROUTERS) {
      it(`${label}: refuses ".." and "a/b" for ${params.join(', ')}, and lets a real id through`, () => {
        const router = create() as Router & { params: Record<string, ParamHandler[] | undefined> }
        for (const name of params) {
          const handlers = router.params[name] ?? []
          expect(handlers, `guard for ${name}`).to.not.be.empty
          expect(outcome(handlers, '..'), `".." for ${name}`).to.be.instanceOf(BadRequestError)
          expect(outcome(handlers, 'a/b'), `"a/b" for ${name}`).to.be.instanceOf(BadRequestError)
          expect(outcome(handlers, '3f2c9e7a-1b4d-4c8e'), `real id for ${name}`).to.equal(undefined)
        }
      })
    }
  })
})
