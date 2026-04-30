import { expect } from 'chai'
import sinon from 'sinon'
import {
  isLocalFsConnector,
  LocalFsResyncDispatcher,
} from '../../../../src/modules/knowledge_base/services/local_fs_resync_dispatcher'
import { localFsWatcherRegistry } from '../../../../src/modules/cli_rpc/socket/local_fs_watcher_registry'

describe('local_fs_resync_dispatcher', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('isLocalFsConnector', () => {
    it('accepts supported local fs connector names', () => {
      expect(isLocalFsConnector('localfs')).to.equal(true)
      expect(isLocalFsConnector('Local FS')).to.equal(true)
      expect(isLocalFsConnector('local_filesystem')).to.equal(true)
      expect(isLocalFsConnector('Folder Sync')).to.equal(true)
    })

    it('rejects non-local connectors', () => {
      expect(isLocalFsConnector('google-drive')).to.equal(false)
      expect(isLocalFsConnector('slack')).to.equal(false)
    })
  })

  describe('LocalFsResyncDispatcher.dispatch', () => {
    it('forwards request to watcher registry and returns replay counters', async () => {
      const dispatcher = new LocalFsResyncDispatcher()
      const request = {
        orgId: 'org-1',
        connectorId: 'connector-1',
        origin: 'CLI',
        fullSync: true,
      }
      const expected = {
        replayedBatches: 3,
        replayedEvents: 7,
        skippedBatches: 1,
      }

      const dispatchStub = sinon
        .stub(localFsWatcherRegistry, 'dispatch')
        .resolves(expected)

      const result = await dispatcher.dispatch(request)

      expect(result).to.deep.equal(expected)
      expect(dispatchStub.calledOnceWithExactly(request)).to.equal(true)
    })
  })
})
