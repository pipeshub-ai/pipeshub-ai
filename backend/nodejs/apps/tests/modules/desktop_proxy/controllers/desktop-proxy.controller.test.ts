import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import {
  fetchLocalFsContent,
  pullLocalFsFileEvents,
} from '../../../../src/modules/desktop_proxy/controllers/desktop-proxy.controller'
import { DesktopProxySocketGateway } from '../../../../src/modules/desktop_proxy/socket/desktop-proxy.gateway'
import {
  DesktopOfflineError,
  DesktopRemoteError,
  DesktopTimeoutError,
} from '../../../../src/modules/desktop_proxy/types/local-fs-pull.types'

const STATUS_BAD_REQUEST = 400
const STATUS_OFFLINE = 409
const STATUS_TIMEOUT = 504
const STATUS_NOT_READY = 503
const STATUS_DESKTOP_FAILED = 502

describe('desktop-proxy.controller', () => {
  let container: Container
  let gateway: any
  let req: any
  let res: any
  let next: sinon.SinonSpy

  beforeEach(() => {
    gateway = {
      isReady: sinon.stub().returns(true),
      requestLocalFsFileEvents: sinon.stub(),
      requestLocalFsContent: sinon.stub(),
    }
    container = new Container()
    container
      .bind<DesktopProxySocketGateway>(DesktopProxySocketGateway)
      .toConstantValue(gateway)

    req = {
      tokenPayload: { orgId: 'org-1', userId: 'user-1' },
      body: {
        connectorId: 'conn-1',
        runId: 'run-1',
        batchIndex: 0,
        mode: 'FULL',
        cursor: null,
        maxEvents: 50,
        timeoutMs: 60_000,
      },
    }
    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      type: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    }
    next = sinon.spy()
  })

  afterEach(() => sinon.restore())

  describe('pullLocalFsFileEvents', () => {
    it('returns the desktop page and never trusts a body-supplied user', async () => {
      const page = {
        connectorId: 'conn-1',
        runId: 'run-1',
        batchIndex: 0,
        cursor: 'c1',
        hasMore: false,
        events: [],
      }
      gateway.requestLocalFsFileEvents.resolves(page)
      req.body.userId = 'someone-else'

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(res.status.calledWith(200)).to.equal(true)
      expect(res.json.firstCall.args[0]).to.deep.equal({
        success: true,
        data: page,
      })
      // orgId/userId must come from the scoped token, or the route becomes a
      // cross-tenant targeting primitive.
      const [orgId, userId] = gateway.requestLocalFsFileEvents.firstCall.args
      expect(orgId).to.equal('org-1')
      expect(userId).to.equal('user-1')
    })

    it('answers 409 when no desktop is connected', async () => {
      gateway.requestLocalFsFileEvents.rejects(new DesktopOfflineError('nope'))

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(res.status.calledWith(STATUS_OFFLINE)).to.equal(true)
      expect(res.json.firstCall.args[0].code).to.equal('DESKTOP_OFFLINE')
      expect(next.called).to.equal(false)
    })

    it('answers 504 when the desktop does not ack in time', async () => {
      gateway.requestLocalFsFileEvents.rejects(new DesktopTimeoutError('slow'))

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(res.status.calledWith(STATUS_TIMEOUT)).to.equal(true)
      expect(res.json.firstCall.args[0].code).to.equal('DESKTOP_TIMEOUT')
    })

    it('answers 502 when the desktop replies with a failure', async () => {
      gateway.requestLocalFsFileEvents.rejects(
        new DesktopRemoteError('ROOT_UNREADABLE', 'folder gone', false),
      )

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(res.status.calledWith(STATUS_DESKTOP_FAILED)).to.equal(true)
      const body = res.json.firstCall.args[0]
      expect(body.code).to.equal('ROOT_UNREADABLE')
      // retryable must survive the hop — the connector decides on it.
      expect(body.error.retryable).to.equal(false)
      expect(next.called).to.equal(false)
    })

    it('answers 503 before the socket namespace is attached', async () => {
      // Routes are mounted before gateway.initialize() runs, so a request that
      // lands in that window must not look like an offline desktop.
      gateway.isReady.returns(false)

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(res.status.calledWith(STATUS_NOT_READY)).to.equal(true)
      expect(gateway.requestLocalFsFileEvents.called).to.equal(false)
    })

    it('answers 400 when the token has no org/user to address', async () => {
      req.tokenPayload = {}

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(res.status.calledWith(STATUS_BAD_REQUEST)).to.equal(true)
      expect(res.json.firstCall.args[0].code).to.equal('NO_TARGET')
      expect(res.json.firstCall.args[0].error.retryable).to.equal(false)
      expect(gateway.requestLocalFsFileEvents.called).to.equal(false)
    })

    it('delegates unexpected errors to the error middleware', async () => {
      gateway.requestLocalFsFileEvents.rejects(new Error('boom'))

      await pullLocalFsFileEvents(container)(req, res, next)

      expect(next.calledOnce).to.equal(true)
    })
  })

  describe('fetchLocalFsContent', () => {
    beforeEach(() => {
      req.body = {
        connectorId: 'conn-1',
        relPath: 'a/b.txt',
        externalRecordId: 'ext-1',
        sha256: null,
        timeoutMs: 60_000,
      }
    })

    it('streams raw bytes rather than JSON', async () => {
      const buf = Buffer.from('hello')
      gateway.requestLocalFsContent.resolves(buf)

      await fetchLocalFsContent(container)(req, res, next)

      expect(res.type.calledWith('application/octet-stream')).to.equal(true)
      expect(res.send.calledWith(buf)).to.equal(true)
    })

    it('answers 409 when the desktop is offline', async () => {
      gateway.requestLocalFsContent.rejects(new DesktopOfflineError('nope'))

      await fetchLocalFsContent(container)(req, res, next)

      expect(res.status.calledWith(STATUS_OFFLINE)).to.equal(true)
      expect(res.json.firstCall.args[0].error.retryable).to.equal(true)
    })

    it('answers 400 when the token has no org/user to address', async () => {
      req.tokenPayload = {}

      await fetchLocalFsContent(container)(req, res, next)

      expect(res.status.calledWith(STATUS_BAD_REQUEST)).to.equal(true)
      expect(res.json.firstCall.args[0].code).to.equal('NO_TARGET')
      expect(res.json.firstCall.args[0].error.retryable).to.equal(false)
      expect(gateway.requestLocalFsContent.called).to.equal(false)
    })
  })
})
