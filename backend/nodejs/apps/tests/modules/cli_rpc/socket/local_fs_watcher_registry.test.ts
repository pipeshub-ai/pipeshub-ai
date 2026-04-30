import { expect } from 'chai'
import { ConflictError } from '../../../../src/libs/errors/http.errors'
import { LocalFsWatcherRegistry } from '../../../../src/modules/cli_rpc/socket/local_fs_watcher_registry'

type MockSocket = {
  id: string
  data: {
    orgId?: string
    watcherConnectorId?: string
  }
  timeout: (timeoutMs: number) => {
    emit: (
      event: string,
      payload: unknown,
      cb: (err: Error | null, ack?: unknown) => void,
    ) => void
  }
}

function createSocket(
  options: {
    id?: string
    orgId?: string
    timeoutImpl?: MockSocket['timeout']
  } = {},
): MockSocket {
  return {
    id: options.id ?? 'socket-1',
    data: {
      orgId: options.orgId ?? 'org-1',
    },
    timeout:
      options.timeoutImpl ??
      (() => ({
        emit: (_event, _payload, cb) =>
          cb(null, { ok: true, replayedBatches: 1, replayedEvents: 2, skippedBatches: 3 }),
      })),
  }
}

describe('LocalFsWatcherRegistry', () => {
  let registry: LocalFsWatcherRegistry

  beforeEach(() => {
    registry = new LocalFsWatcherRegistry()
  })

  it('registers and tracks active watcher', () => {
    const socket = createSocket()
    registry.register(socket as never, 'connector-1')

    expect(registry.hasActiveWatcher('org-1', 'connector-1')).to.equal(true)
    expect(socket.data.watcherConnectorId).to.equal('connector-1')
  })

  it('rejects registration when orgId or connectorId is missing', () => {
    const socketMissingOrg = createSocket({ orgId: '   ' })
    expect(() => registry.register(socketMissingOrg as never, 'connector-1')).to.throw(
      ConflictError,
      'Watcher registration requires orgId and connectorId',
    )

    const socket = createSocket()
    expect(() => registry.register(socket as never, '   ')).to.throw(
      ConflictError,
      'Watcher registration requires orgId and connectorId',
    )
  })

  it('rejects duplicate watcher from another socket', () => {
    const first = createSocket({ id: 'socket-1' })
    const second = createSocket({ id: 'socket-2' })

    registry.register(first as never, 'connector-1')
    expect(() => registry.register(second as never, 'connector-1')).to.throw(
      ConflictError,
      'A Local FS watcher is already active for this connector. Stop the other `pipeshub run` first.',
    )
  })

  it('unregisters only matching socket', () => {
    const first = createSocket({ id: 'socket-1' })
    const second = createSocket({ id: 'socket-2' })
    registry.register(first as never, 'connector-1')

    registry.unregister(second as never)
    expect(registry.hasActiveWatcher('org-1', 'connector-1')).to.equal(true)

    registry.unregister(first as never)
    expect(registry.hasActiveWatcher('org-1', 'connector-1')).to.equal(false)
    expect(first.data.watcherConnectorId).to.equal(undefined)
  })

  it('dispatches localfs:resync and returns ack counters', async () => {
    const socket = createSocket({
      timeoutImpl: () => ({
        emit: (event, payload, cb) => {
          expect(event).to.equal('localfs:resync')
          expect(payload).to.deep.equal({
            connectorId: 'connector-1',
            fullSync: true,
            origin: 'CLI',
          })
          cb(null, {
            ok: true,
            replayedBatches: 4,
            replayedEvents: 15,
            skippedBatches: 2,
          })
        },
      }),
    })
    registry.register(socket as never, 'connector-1')

    const result = await registry.dispatch({
      orgId: 'org-1',
      connectorId: 'connector-1',
      origin: 'CLI',
      fullSync: true,
    })

    expect(result).to.deep.equal({
      replayedBatches: 4,
      replayedEvents: 15,
      skippedBatches: 2,
    })
  })

  it('dispatch fails when watcher is not active', async () => {
    try {
      await registry.dispatch({
        orgId: 'org-1',
        connectorId: 'missing',
      })
      expect.fail('Expected dispatch to throw when watcher is missing')
    } catch (error) {
      expect(error).to.be.instanceOf(ConflictError)
      expect((error as Error).message).to.equal(
        'No active Local FS watcher for this connector. Start `pipeshub run` first.',
      )
    }
  })

  it('dispatch surfaces socket error and nack message', async () => {
    const erroredSocket = createSocket({
      timeoutImpl: () => ({
        emit: (_event, _payload, cb) => cb(new Error('socket emit failed')),
      }),
    })
    registry.register(erroredSocket as never, 'connector-1')

    try {
      await registry.dispatch({
        orgId: 'org-1',
        connectorId: 'connector-1',
      })
      expect.fail('Expected dispatch to surface socket error')
    } catch (error) {
      expect((error as Error).message).to.equal('socket emit failed')
    }

    registry.clear()
    const nackSocket = createSocket({
      timeoutImpl: () => ({
        emit: (_event, _payload, cb) =>
          cb(null, { ok: false, error: { message: 'watcher rejected request' } }),
      }),
    })
    registry.register(nackSocket as never, 'connector-1')

    try {
      await registry.dispatch({
        orgId: 'org-1',
        connectorId: 'connector-1',
      })
      expect.fail('Expected dispatch to throw for nack ack')
    } catch (error) {
      expect(error).to.be.instanceOf(ConflictError)
      expect((error as Error).message).to.equal('watcher rejected request')
    }
  })

  it('dispatch times out when watcher does not respond', async () => {
    const socket = createSocket({
      timeoutImpl: () => ({
        emit: () => {
          // Intentionally do not invoke callback to trigger timeout path.
        },
      }),
    })
    registry.register(socket as never, 'connector-1')

    try {
      await registry.dispatch(
        {
          orgId: 'org-1',
          connectorId: 'connector-1',
        },
        5,
      )
      expect.fail('Expected dispatch timeout')
    } catch (error) {
      expect(error).to.be.instanceOf(ConflictError)
      expect((error as Error).message).to.equal(
        'Local FS watcher did not respond in time. Start `pipeshub run` again and retry.',
      )
    }
  })
})
