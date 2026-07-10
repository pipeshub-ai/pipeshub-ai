import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ChatKbFiltersMigration } from '../../../../../src/modules/configuration_manager/services/migrations/chat_kb_filters.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import * as encryptorModule from '../../../../../src/libs/encryptor/encryptor';
import { Conversation } from '../../../../../src/modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../../../src/modules/enterprise_search/schema/agent.conversation.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (existingFlag: string | null = null, kbAppsMigrationFlag: string | null = null) => ({
  get: sinon.stub().callsFake((path: string) => {
    if (path === configPaths.chatKbFiltersMigration) {
      return Promise.resolve(existingFlag);
    }
    if (path === configPaths.kbAppsMigrationDone) {
      return Promise.resolve(kbAppsMigrationFlag);
    }
    return Promise.resolve(null);
  }),
  set: sinon.stub().resolves(),
});

const noBackoff = { maxAttempts: 1, initialDelayMs: 0 };

const emptyCursor = () => ({
  [Symbol.asyncIterator]: async function* () {},
});

const cursorFromDocs = (docs: any[]) => ({
  [Symbol.asyncIterator]: async function* () {
    for (const doc of docs) {
      yield doc;
    }
  },
});

describe('ChatKbFiltersMigration', () => {
  let mockEncService: any;
  let conversationCursor: AsyncIterable<any>;
  let agentCursor: AsyncIterable<any>;

  const setupModels = () => {
    sinon.stub(Conversation, 'find').callsFake(
      () =>
        ({
          cursor: sinon.stub().callsFake(() => conversationCursor),
        }) as any,
    );
    sinon.stub(AgentConversation, 'find').callsFake(
      () =>
        ({
          cursor: sinon.stub().callsFake(() => agentCursor),
        }) as any,
    );
  };

  const setupKbAppsReady = () => {
    mockEncService.decrypt.returns(JSON.stringify({ done: true }));
  };

  beforeEach(() => {
    conversationCursor = emptyCursor();
    agentCursor = emptyCursor();
    mockEncService = { decrypt: sinon.stub(), encrypt: sinon.stub() };
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns(mockEncService);
    setupModels();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('migration flag', () => {
    it('skips when flag is "true"', async () => {
      const kv = makeKvStore('true', null);
      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(kv.get.calledWith(configPaths.chatKbFiltersMigration)).to.equal(true);
      expect(result).to.deep.equal({ conversationsUpdated: 0, messagesUpdated: 0, errored: 0 });
      expect((Conversation.find as sinon.SinonStub).called).to.equal(false);
    });

    it('proceeds when flag is null', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      await m.run();

      expect((Conversation.find as sinon.SinonStub).calledOnce).to.equal(true);
      expect((AgentConversation.find as sinon.SinonStub).calledOnce).to.equal(true);
    });

    it('proceeds even when KV get throws (idempotency safety net)', async () => {
      const logger = makeLogger();
      const kv = {
        get: sinon.stub().callsFake((path: string) => {
          if (path === configPaths.chatKbFiltersMigration) {
            return Promise.reject(new Error('redis down'));
          }
          if (path === configPaths.kbAppsMigrationDone) {
            return Promise.resolve(JSON.stringify('encrypted-value'));
          }
          return Promise.resolve(null);
        }),
        set: sinon.stub().resolves(),
      };
      setupKbAppsReady();

      const m = new ChatKbFiltersMigration(logger as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      await m.run();

      expect(logger.warn.called).to.equal(true);
      expect((Conversation.find as sinon.SinonStub).calledOnce).to.equal(true);
    });
  });

  describe('KB-apps migration dependency', () => {
    it('waits for KB-apps migration and proceeds when done:true', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const m = new ChatKbFiltersMigration(logger as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      await m.run();

      expect(logger.info.calledWith(sinon.match(/KB-apps migration confirmed complete/))).to.equal(true);
    });

    it('returns early when KB-apps migration not ready within retry window', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null, null);
      const m = new ChatKbFiltersMigration(
        logger as any,
        kv as any,
        'aes-256-gcm',
        'a'.repeat(64),
        noBackoff,
      );
      const result = await m.run();

      expect(result).to.deep.equal({ conversationsUpdated: 0, messagesUpdated: 0, errored: 0 });
      expect(kv.set.called).to.equal(false);
    });

    it('handles decryption errors gracefully during backoff', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null, 'invalid-encrypted-data');
      mockEncService.decrypt.throws(new Error('Decryption failed'));

      const m = new ChatKbFiltersMigration(
        logger as any,
        kv as any,
        'aes-256-gcm',
        'a'.repeat(64),
        noBackoff,
      );
      const result = await m.run();

      expect(result.conversationsUpdated).to.equal(0);
      expect(logger.warn.calledWith(sinon.match(/Failed to read KB-apps migration flag/))).to.equal(true);
    });
  });

  describe('successful migration', () => {
    it('migrates conversations with stale KB filters', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const mockDoc = {
        _id: 'conv-1',
        messages: [
          {
            appliedFilters: {
              kb: [{ id: 'kb-1', name: 'KB 1', nodeType: 'recordGroup', connector: 'localkb' }],
              apps: [],
            },
          },
        ],
        markModified: sinon.stub(),
        save: sinon.stub().resolves(),
      };
      conversationCursor = cursorFromDocs([mockDoc]);
      agentCursor = emptyCursor();

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(result).to.deep.equal({ conversationsUpdated: 1, messagesUpdated: 1, errored: 0 });
      expect(mockDoc.markModified.calledWith('messages')).to.equal(true);
      expect(kv.set.calledWith(configPaths.chatKbFiltersMigration, 'true')).to.equal(true);
      expect(mockDoc.messages[0].appliedFilters.apps[0].nodeType).to.equal('app');
    });

    it('moves multiple stale entries from kb to apps', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const mockDoc = {
        _id: 'conv-2',
        messages: [
          {
            appliedFilters: {
              kb: [
                { id: 'kb-1', name: 'KB 1', nodeType: 'recordGroup', connector: 'localkb' },
                { id: 'kb-2', name: 'KB 2', nodeType: 'recordGroup', connector: 'localkb' },
                { id: 'doc-1', name: 'Doc 1', nodeType: 'document', connector: 'localkb' },
              ],
              apps: [{ id: 'app-1', name: 'App 1', nodeType: 'app', connector: 'googledrive' }],
            },
          },
        ],
        markModified: sinon.stub(),
        save: sinon.stub().resolves(),
      };
      conversationCursor = cursorFromDocs([mockDoc]);

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      await m.run();

      expect(mockDoc.messages[0].appliedFilters.kb).to.have.lengthOf(1);
      expect(mockDoc.messages[0].appliedFilters.apps).to.have.lengthOf(3);
    });

    it('skips conversations with no stale KB filters', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const mockDoc = {
        _id: 'conv-3',
        messages: [
          {
            appliedFilters: {
              kb: [{ id: 'doc-1', name: 'Doc 1', nodeType: 'document', connector: 'localkb' }],
              apps: [{ id: 'app-1', name: 'App 1', nodeType: 'app', connector: 'googledrive' }],
            },
          },
        ],
        markModified: sinon.stub(),
        save: sinon.stub().resolves(),
      };
      conversationCursor = cursorFromDocs([mockDoc]);

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(result.conversationsUpdated).to.equal(0);
      expect(mockDoc.save.called).to.equal(false);
    });

    it('processes both Conversation and AgentConversation models', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const makeDoc = (id: string) => ({
        _id: id,
        messages: [
          {
            appliedFilters: {
              kb: [{ id: `kb-${id}`, name: 'KB', nodeType: 'recordGroup', connector: 'localkb' }],
              apps: [],
            },
          },
        ],
        markModified: sinon.stub(),
        save: sinon.stub().resolves(),
      });

      conversationCursor = cursorFromDocs([makeDoc('conv-1')]);
      agentCursor = cursorFromDocs([makeDoc('agent-conv-1')]);

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(result.conversationsUpdated).to.equal(2);
      expect(result.messagesUpdated).to.equal(2);
    });
  });

  describe('error handling', () => {
    it('logs error and does not set flag when save fails', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const mockDoc = {
        _id: 'conv-fail',
        messages: [
          {
            appliedFilters: {
              kb: [{ id: 'kb-1', name: 'KB 1', nodeType: 'recordGroup', connector: 'localkb' }],
              apps: [],
            },
          },
        ],
        markModified: sinon.stub(),
        save: sinon.stub().rejects(new Error('DB write failed')),
      };
      conversationCursor = cursorFromDocs([mockDoc]);

      const m = new ChatKbFiltersMigration(logger as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(result.errored).to.equal(1);
      expect(kv.set.called).to.equal(false);
      expect(logger.error.calledWith(sinon.match(/Failed to migrate chat KB filters/))).to.equal(true);
    });

    it('logs error but continues when flag persistence fails', async () => {
      const logger = makeLogger();
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      kv.set.rejects(new Error('etcd unavailable'));
      setupKbAppsReady();

      const m = new ChatKbFiltersMigration(logger as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(result.errored).to.equal(0);
      expect(logger.error.calledWith(sinon.match(/Failed to persist.*completion flag/))).to.equal(true);
    });
  });

  describe('edge cases', () => {
    it('handles messages with no appliedFilters', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const mockDoc = {
        _id: 'conv-no-filters',
        messages: [{ appliedFilters: undefined }, { appliedFilters: null }],
        markModified: sinon.stub(),
        save: sinon.stub().resolves(),
      };
      conversationCursor = cursorFromDocs([mockDoc]);

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      const result = await m.run();

      expect(result.conversationsUpdated).to.equal(0);
      expect(mockDoc.save.called).to.equal(false);
    });

    it('initializes apps array when undefined', async () => {
      const kv = makeKvStore(null, JSON.stringify('encrypted-value'));
      setupKbAppsReady();

      const mockDoc = {
        _id: 'conv-no-apps',
        messages: [
          {
            appliedFilters: {
              kb: [{ id: 'kb-1', name: 'KB 1', nodeType: 'recordGroup', connector: 'localkb' }],
              apps: undefined,
            },
          },
        ],
        markModified: sinon.stub(),
        save: sinon.stub().resolves(),
      };
      conversationCursor = cursorFromDocs([mockDoc]);

      const m = new ChatKbFiltersMigration(makeLogger() as any, kv as any, 'aes-256-gcm', 'a'.repeat(64));
      await m.run();

      expect(mockDoc.messages[0].appliedFilters.apps).to.have.lengthOf(1);
      expect(mockDoc.messages[0].appliedFilters.apps[0].nodeType).to.equal('app');
    });
  });
});
