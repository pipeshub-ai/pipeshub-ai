import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ConnectorsCrawlingService } from '../../../../../src/modules/crawling_manager/services/connectors/connectors';
import type { SyncEventProducer } from '../../../../../src/modules/knowledge_base/services/sync_events.service';
import { CrawlingScheduleType } from '../../../../../src/modules/crawling_manager/schema/enums';
import type { ICrawlingSchedule } from '../../../../../src/modules/crawling_manager/schema/interface';
import { registerDesktopPresence } from '../../../../../src/libs/services/desktop-presence.provider';
const makePresence = (online: boolean | null, connected: boolean | null = null) => ({
  isLocalFsDesktopOnline: sinon.stub().returns(online),
  isDesktopConnected: sinon.stub().returns(connected),
});

describe('ConnectorsCrawlingService', () => {
  const orgId = 'org-1';
  const userId = 'user-1';
  const connectorId = 'conn-1';
  /** crawl() does not read schedule fields; minimal shape matches worker tests */
  const scheduleConfig = {
    scheduleType: CrawlingScheduleType.DAILY,
  } as ICrawlingSchedule;

  // Presence is a module-level singleton; a sibling file in the same mocha
  // worker (app.test.ts boots the real gateway) can leave it registered.
  beforeEach(() => {
    registerDesktopPresence(null);
  });

  afterEach(() => {
    sinon.restore();
  });

  function makeService(publishEvent: sinon.SinonStub): ConnectorsCrawlingService {
    const syncEvents = { publishEvent } as unknown as SyncEventProducer;
    return new ConnectorsCrawlingService(syncEvents);
  }

  describe('crawl', () => {
    it('publishes a sync event for non-Local FS connectors', async () => {
      const publishEvent = sinon.stub().resolves();
      const service = makeService(publishEvent);

      const result = await service.crawl(
        orgId,
        userId,
        scheduleConfig,
        'slack',
        connectorId,
      );

      expect(result).to.deep.equal({ success: true });
      expect(publishEvent.calledOnce).to.be.true;
      const event = publishEvent.firstCall.args[0];
      expect(event.eventType).to.equal('slack.resync');
      expect(event.payload.orgId).to.equal(orgId);
      expect(event.payload.connector).to.equal('slack');
      expect(event.payload.connectorId).to.equal(connectorId);
      expect(event.payload.origin).to.equal('CONNECTOR');
    });

    // Local FS is server-driven like every other connector now: a scheduled
    // tick must reach run_sync, which pulls from the user's desktop.
    ;['localfs', 'Local FS', '  local_fs  '].forEach((connectorName) => {
      it(`publishes a scheduled sync for Local FS ("${connectorName}")`, async () => {
        const publishEvent = sinon.stub().resolves();
        const service = makeService(publishEvent);

        const result = await service.crawl(
          orgId,
          userId,
          scheduleConfig,
          connectorName,
          connectorId,
        );

        expect(result).to.deep.equal({ success: true });
        expect(publishEvent.calledOnce).to.be.true;
      });
    });

    it('rethrows when publishEvent fails', async () => {
      const err = new Error('kafka down');
      const publishEvent = sinon.stub().rejects(err);
      const service = makeService(publishEvent);

      try {
        await service.crawl(orgId, userId, scheduleConfig, 'drive', connectorId);
        expect.fail('expected crawl to reject');
      } catch (e) {
        expect(e).to.be.instanceOf(Error);
        expect((e as Error).message).to.equal('kafka down');
      }
      expect(publishEvent.calledOnce).to.be.true;
    });
  });
});

describe('ConnectorsCrawlingService (Local FS desktop presence)', () => {
  const orgId = 'org-1';
  const userId = 'owner-1';
  const connectorId = 'conn-1';
  const scheduleConfig = {
    scheduleType: CrawlingScheduleType.DAILY,
  } as ICrawlingSchedule;

  afterEach(() => {
    sinon.restore();
    registerDesktopPresence(null);
  });

  function makeService(publishEvent: sinon.SinonStub): ConnectorsCrawlingService {
    return new ConnectorsCrawlingService({ publishEvent } as unknown as SyncEventProducer);
  }

  it('skips quietly, without throwing, when the owner desktop is offline', async () => {
    const presence = makePresence(false);
    registerDesktopPresence(presence);
    const publishEvent = sinon.stub().resolves();

    const result = await makeService(publishEvent).crawl(orgId, userId, scheduleConfig, 'Local FS', connectorId);

    expect(result).to.deep.equal({ success: true });
    expect(publishEvent.called).to.be.false;
    expect(presence.isLocalFsDesktopOnline.calledOnceWithExactly(orgId, userId, connectorId)).to.be.true;
  });

  it('publishes when the desktop is online', async () => {
    registerDesktopPresence(makePresence(true));
    const publishEvent = sinon.stub().resolves();

    await makeService(publishEvent).crawl(orgId, userId, scheduleConfig, 'localfs', connectorId);

    expect(publishEvent.calledOnce).to.be.true;
  });

  it('publishes when presence cannot tell', async () => {
    registerDesktopPresence(makePresence(null));
    const publishEvent = sinon.stub().resolves();

    await makeService(publishEvent).crawl(orgId, userId, scheduleConfig, 'Local FS', connectorId);

    expect(publishEvent.calledOnce).to.be.true;
  });

  it('never consults presence for other connectors', async () => {
    const presence = makePresence(false);
    registerDesktopPresence(presence);
    const publishEvent = sinon.stub().resolves();

    await makeService(publishEvent).crawl(orgId, userId, scheduleConfig, 'slack', connectorId);

    expect(publishEvent.calledOnce).to.be.true;
    expect(presence.isLocalFsDesktopOnline.called).to.be.false;
  });
});
