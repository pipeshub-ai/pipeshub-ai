import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { LocalFsRelay } from '../../../../src/modules/desktop_proxy/socket/local-fs-relay'
import {
  DesktopOfflineError,
  DesktopRemoteError,
  DesktopTimeoutError,
} from '../../../../src/modules/desktop_proxy/types/local-fs-pull.types'

interface FakeSocket {
  data: { orgId: string; userId: string; deviceId?: string }
  connected: boolean
  timeout: sinon.SinonStub
  emitWithAck: sinon.SinonStub
}

const PULL_PAYLOAD = {
  connectorId: 'conn-1',
  runId: 'run-1',
  batchIndex: 0,
  mode: 'FULL' as const,
  cursor: null,
  maxEvents: 50,
  timeoutMs: 60_000,
}

const CONTENT_PAYLOAD = {
  connectorId: 'conn-1',
  relPath: 'a/b.txt',
  externalRecordId: 'ext-1',
  sha256: null,
  timeoutMs: 60_000,
}

function makeSocket(orgId = 'org-1', userId = 'user-1'): FakeSocket {
  const socket: Partial<FakeSocket> = {
    data: { orgId, userId },
    connected: true,
    emitWithAck: sinon.stub(),
  }
  socket.timeout = sinon.stub().returns({ emitWithAck: socket.emitWithAck })
  return socket as FakeSocket
}

function asRelaySocket(socket: FakeSocket) {
  return socket as never
}

/** chai-as-promised is not wired into this suite, so assert rejections by hand. */
async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected the promise to reject')
}

describe('LocalFsRelay', () => {
  let relay: LocalFsRelay

  beforeEach(() => {
    relay = new LocalFsRelay()
  })

  afterEach(() => sinon.restore())

  describe('registration', () => {
    it('accepts a first claim and rejects a second machine', () => {
      const first = makeSocket()
      const second = makeSocket()

      const firstAck = relay.register(asRelaySocket(first), ['conn-1'], 'dev-a')
      const secondAck = relay.register(asRelaySocket(second), ['conn-1'], 'dev-b')

      expect(firstAck.accepted).to.deep.equal(['conn-1'])
      expect(firstAck.rejected).to.deep.equal([])
      expect(secondAck.accepted).to.deep.equal([])
      expect(secondAck.rejected).to.deep.equal([
        { connectorId: 'conn-1', reason: 'ALREADY_REGISTERED' },
      ])
    })

    it('frees the claim when the holder disconnects', () => {
      const first = makeSocket()
      const second = makeSocket()
      relay.register(asRelaySocket(first), ['conn-1'], 'dev-a')

      first.connected = false
      relay.handleDisconnect(asRelaySocket(first))

      const ack = relay.register(asRelaySocket(second), ['conn-1'], 'dev-b')
      expect(ack.accepted).to.deep.equal(['conn-1'])
    })

    it('re-registering the same socket is idempotent, not a self-conflict', () => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      const again = relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      expect(again.accepted).to.deep.equal(['conn-1'])
      expect(again.rejected).to.deep.equal([])
    })
  })

  describe('requestFileEvents', () => {
    it('emits to the registered socket and returns its ack', async () => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      socket.emitWithAck.resolves({
        ok: true,
        connectorId: 'conn-1',
        runId: 'run-1',
        batchIndex: 0,
        deviceId: 'dev-a',
        cursor: 'c1',
        hasMore: false,
        events: [],
      })

      const result = await relay.requestFileEvents(
        'org-1',
        'user-1',
        'conn-1',
        PULL_PAYLOAD,
      )

      expect(socket.timeout.firstCall.args[0]).to.equal(65_000)
      expect(socket.emitWithAck.firstCall.args[0]).to.equal(
        'localfs:file-events:pull',
      )
      expect(result.cursor).to.equal('c1')
      expect(result.deviceId).to.equal('dev-a')
      expect((result as { ok?: boolean }).ok).to.equal(undefined)
    })

    it('stamps the registered deviceId when the ack omits it', async () => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      socket.emitWithAck.resolves({
        ok: true,
        connectorId: 'conn-1',
        runId: 'run-1',
        batchIndex: 0,
        hasMore: false,
        events: [],
      })

      const result = await relay.requestFileEvents(
        'org-1',
        'user-1',
        'conn-1',
        PULL_PAYLOAD,
      )
      expect(result.deviceId).to.equal('dev-a')
    })

    it('throws DesktopOfflineError when no desktop registered the connector', async () => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-other'], 'dev-a')

      const error = await rejection(
        relay.requestFileEvents('org-1', 'user-1', 'conn-1', PULL_PAYLOAD),
      )
      expect(error).to.be.instanceOf(DesktopOfflineError)
    })

    it('never falls back to another user in the same org', async () => {
      const socket = makeSocket('org-1', 'user-1')
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')

      const error = await rejection(
        relay.requestFileEvents('org-1', 'user-2', 'conn-1', PULL_PAYLOAD),
      )
      expect(error).to.be.instanceOf(DesktopOfflineError)
      expect(socket.emitWithAck.called).to.equal(false)
    })

    it('maps a socket.io ack timeout to DesktopTimeoutError', async () => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      socket.emitWithAck.rejects(new Error('operation has timed out'))

      const error = await rejection(
        relay.requestFileEvents('org-1', 'user-1', 'conn-1', PULL_PAYLOAD),
      )
      expect(error).to.be.instanceOf(DesktopTimeoutError)
    })

    it('maps an ok:false ack to DesktopRemoteError with retryable passed through', async () => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      socket.emitWithAck.resolves({
        ok: false,
        runId: 'run-1',
        batchIndex: 0,
        error: { code: 'CURSOR_UNKNOWN', message: 'gone', retryable: false },
      })

      const error = await rejection(
        relay.requestFileEvents('org-1', 'user-1', 'conn-1', PULL_PAYLOAD),
      )
      expect(error).to.be.instanceOf(DesktopRemoteError)
      expect((error as DesktopRemoteError).code).to.equal('CURSOR_UNKNOWN')
      expect((error as DesktopRemoteError).retryable).to.equal(false)
    })
  })

  describe('requestContent', () => {
    const registerAndAck = (size: number) => {
      const socket = makeSocket()
      relay.register(asRelaySocket(socket), ['conn-1'], 'dev-a')
      socket.emitWithAck.resolves({
        ok: true,
        requestId: 'ignored',
        size,
        mimeType: 'text/plain',
      })
      return socket
    }

    const requestIdFrom = (socket: FakeSocket): string =>
      (socket.emitWithAck.firstCall.args[1] as { requestId: string }).requestId

    it('reassembles multi-frame content byte-identically', async () => {
      const body = Buffer.from('0123456789abcdef')
      const socket = registerAndAck(body.length)

      const promise = relay.requestContent(
        'org-1',
        'user-1',
        'conn-1',
        CONTENT_PAYLOAD,
      )
      // Let the emitWithAck promise settle so the relay records expectedSize.
      await new Promise((r) => setImmediate(r))
      const requestId = requestIdFrom(socket)

      relay.handleContentChunk(asRelaySocket(socket), {
        requestId,
        seq: 0,
        data: body.subarray(0, 7),
      })
      relay.handleContentChunk(asRelaySocket(socket), {
        requestId,
        seq: 1,
        data: body.subarray(7),
        final: true,
      })

      const received = await promise
      expect(received.equals(body)).to.equal(true)
    })

    it('rejects when the desktop aborts mid-transfer', async () => {
      const socket = registerAndAck(1024)
      const promise = relay.requestContent(
        'org-1',
        'user-1',
        'conn-1',
        CONTENT_PAYLOAD,
      )
      await new Promise((r) => setImmediate(r))
      const requestId = requestIdFrom(socket)

      relay.handleContentAbort(asRelaySocket(socket), {
        requestId,
        error: { code: 'FILE_GONE', message: 'deleted', retryable: false },
      })

      const error = await rejection(promise)
      expect((error as DesktopRemoteError).code).to.equal('FILE_GONE')
      expect((error as DesktopRemoteError).retryable).to.equal(false)
    })

    it('frees the buffer and rejects when the desktop disconnects mid-transfer', async () => {
      const socket = registerAndAck(1024)
      const promise = relay.requestContent(
        'org-1',
        'user-1',
        'conn-1',
        CONTENT_PAYLOAD,
      )
      await new Promise((r) => setImmediate(r))
      const requestId = requestIdFrom(socket)
      relay.handleContentChunk(asRelaySocket(socket), {
        requestId,
        seq: 0,
        data: Buffer.alloc(512),
      })

      socket.connected = false
      relay.handleDisconnect(asRelaySocket(socket))

      expect(await rejection(promise)).to.be.instanceOf(DesktopOfflineError)
    })

    it('rejects a short transfer rather than returning a truncated file', async () => {
      const socket = registerAndAck(100)
      const promise = relay.requestContent(
        'org-1',
        'user-1',
        'conn-1',
        CONTENT_PAYLOAD,
      )
      await new Promise((r) => setImmediate(r))
      const requestId = requestIdFrom(socket)

      relay.handleContentChunk(asRelaySocket(socket), {
        requestId,
        seq: 0,
        data: Buffer.alloc(40),
        final: true,
      })

      const error = await rejection(promise)
      expect((error as DesktopRemoteError).code).to.equal(
        'CONTENT_SIZE_MISMATCH',
      )
    })

    it('refuses an oversize file up front instead of buffering it', async () => {
      registerAndAck(500 * 1024 * 1024)

      const error = await rejection(
        relay.requestContent('org-1', 'user-1', 'conn-1', CONTENT_PAYLOAD),
      )
      expect((error as DesktopRemoteError).code).to.equal('CONTENT_TOO_LARGE')
      expect((error as DesktopRemoteError).retryable).to.equal(false)
    })

    it('caps concurrent transfers per device', async () => {
      const socket = registerAndAck(1024)
      const started = [
        relay.requestContent('org-1', 'user-1', 'conn-1', CONTENT_PAYLOAD),
        relay.requestContent('org-1', 'user-1', 'conn-1', CONTENT_PAYLOAD),
        relay.requestContent('org-1', 'user-1', 'conn-1', CONTENT_PAYLOAD),
      ]

      const error = await rejection(
        relay.requestContent('org-1', 'user-1', 'conn-1', CONTENT_PAYLOAD),
      )
      expect((error as DesktopRemoteError).code).to.equal('CONTENT_BUSY')

      socket.connected = false
      relay.handleDisconnect(asRelaySocket(socket))
      await Promise.allSettled(started)
    })
  })
})
