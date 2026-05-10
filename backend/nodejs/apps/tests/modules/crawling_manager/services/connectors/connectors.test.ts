import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ConnectorsCrawlingService } from '../../../../../src/modules/crawling_manager/services/connectors/connectors';
import type { SyncEventProducer } from '../../../../../src/modules/knowledge_base/services/sync_events.service';
import { CrawlingScheduleType } from '../../../../../src/modules/crawling_manager/schema/enums';
import type { ICrawlingSchedule } from '../../../../../src/modules/crawling_manager/schema/interface';

describe('ConnectorsCrawlingService', () => {
  const orgId = 'org-1';
  const userId = 'user-1';
  const connectorId = 'conn-1';
  /** crawl() does not read schedule fields; minimal shape matches worker tests */
  const scheduleConfig = {
    scheduleType: CrawlingScheduleType.DAILY,
  } as ICrawlingSchedule;

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

    it('does not publish for Local FS (canonical key)', async () => {
      const publishEvent = sinon.stub().resolves();
      const service = makeService(publishEvent);

      const result = await service.crawl(
        orgId,
        userId,
        scheduleConfig,
        'localfs',
        connectorId,
      );

      expect(result).to.deep.equal({ success: true });
      expect(publishEvent.called).to.be.false;
    });

    it('does not publish for Local FS (display name with spaces)', async () => {
      const publishEvent = sinon.stub().resolves();
      const service = makeService(publishEvent);

      await service.crawl(
        orgId,
        userId,
        scheduleConfig,
        'Local FS',
        connectorId,
      );

      expect(publishEvent.called).to.be.false;
    });

    it('does not publish for Local FS (underscores / extra spaces)', async () => {
      const publishEvent = sinon.stub().resolves();
      const service = makeService(publishEvent);

      await service.crawl(
        orgId,
        userId,
        scheduleConfig,
        '  local_fs  ',
        connectorId,
      );

      expect(publishEvent.called).to.be.false;
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
