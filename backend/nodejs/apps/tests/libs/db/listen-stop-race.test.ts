import { expect } from 'chai'
import sinon from 'sinon'
import { ListenHub, PgListener } from '../../../src/libs/db/listen'

describe('PgListener.stop while start is still subscribing', () => {
  afterEach(() => sinon.restore())

  it('unsubscribes once the subscription exists instead of leaving it on the shared connection', async () => {
    let finish: (unsubscribe: () => Promise<void>) => void = () => undefined
    const subscribed = new Promise<() => Promise<void>>((resolve) => {
      finish = resolve
    })
    const unsubscribe = sinon.stub().resolves()
    sinon.stub(ListenHub, 'forUrl').returns({ subscribe: () => subscribed } as unknown as ListenHub)
    const listener = new PgListener('postgresql://x@127.0.0.1:1/db', 'chan', () => undefined)

    const started = listener.start()
    const stopped = listener.stop() // closes before the subscription resolved
    finish(unsubscribe)
    await Promise.all([started, stopped])

    expect(unsubscribe.calledOnce).to.equal(true)
  })

  it('a stop after a failed start does not throw', async () => {
    sinon.stub(ListenHub, 'forUrl').returns({ subscribe: () => Promise.reject(new Error('down')) } as unknown as ListenHub)
    const listener = new PgListener('postgresql://x@127.0.0.1:1/db', 'chan', () => undefined)
    await listener.start().catch(() => undefined)
    await listener.stop()
  })
})
