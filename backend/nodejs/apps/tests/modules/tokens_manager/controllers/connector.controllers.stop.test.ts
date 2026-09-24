import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils';
import {
  stopConnectorSync,
  resyncConnectorRecords,
} from '../../../../src/modules/tokens_manager/controllers/connector.controllers';

/**
 * Stopping a sync, and refusing a duplicate request.
 *
 * Stop is deliberately not behind the busy guard — it has to work precisely
 * when the connector *is* busy, and against a lock a crash left stuck. The
 * guard belongs on the resync path instead, and it gained QUEUED when queueing
 * was added: without that a queued connector still offered a second request
 * that the backend would only refuse later.
 */
describe('tokens_manager/controllers connector stop + busy guard', () => {
  let appConfig: any;
  let req: any;
  let res: any;
  let next: sinon.SinonStub;

  beforeEach(() => {
    appConfig = { connectorBackend: 'http://connector-backend:8088' };
    req = {
      user: { userId: 'u-1', orgId: 'o-1', role: 'admin' },
      params: { connectorId: 'c-1' },
      query: {},
      body: {},
      headers: {},
    };
    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    };
    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('stopConnectorSync', () => {
    it('posts to the connector service stop route', async () => {
      const exec = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({
          statusCode: 200,
          data: { success: true, stopped: true, status: 'SYNCING' },
        } as any);

      await stopConnectorSync(appConfig)(req, res, next);

      expect(exec.calledOnce).to.be.true;
      expect(exec.firstCall.args[0]).to.equal(
        'http://connector-backend:8088/api/v1/connectors/c-1/sync/stop',
      );
      expect(next.called, 'stop should not have errored').to.be.false;
    });

    it('hands the backend answer back untouched', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { success: true, stopped: false, status: 'IDLE' },
      } as any);

      await stopConnectorSync(appConfig)(req, res, next);

      const body = res.json.firstCall.args[0];
      // stopped:false is a real answer, not a failure — the caller distinguishes
      // "asked it to stop" from "it had already stopped".
      expect(body.stopped).to.equal(false);
      expect(body.status).to.equal('IDLE');
    });

    it('rejects an unauthenticated caller', async () => {
      req.user = {};
      await stopConnectorSync(appConfig)(req, res, next);
      expect(next.calledOnce).to.be.true;
    });

    it('does not consult the connector before stopping it', async () => {
      // The busy guard would refuse exactly the case stop exists for.
      const exec = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({
          statusCode: 200,
          data: { success: true, stopped: true },
        } as any);

      await stopConnectorSync(appConfig)(req, res, next);

      expect(exec.callCount).to.equal(1);
      expect(exec.firstCall.args[0]).to.contain('/sync/stop');
    });
  });

  describe('the busy guard on resync', () => {
    // Two different reads happen before the guard: the connector must appear in
    // /connectors/active, and only then is its status inspected. Stubbing one
    // shape for both made every refusal test pass on the wrong rejection.
    let recordRelationService: any;

    beforeEach(() => {
      req.body = { connectorName: 'gmail' };
      recordRelationService = {
        resyncConnectorRecords: sinon.stub().resolves({ success: true }),
      };
    });

    const run = async (status: string, isLocked = false) => {
      const exec = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .callsFake(async (uri: string) => {
          if (uri.endsWith('/connectors/active')) {
            return {
              statusCode: 200,
              data: { connectors: [{ _key: 'c-1' }] },
            } as any;
          }
          return {
            statusCode: 200,
            data: { connector: { status, isLocked, isActive: true } },
          } as any;
        });
      await resyncConnectorRecords(recordRelationService, appConfig)(
        req,
        res,
        next,
      );
      return exec;
    };

    const refuses = async (status: string, isLocked = false) => {
      await run(status, isLocked);
      expect(next.calledOnce, `${status} should have been refused`).to.be.true;
      expect(
        recordRelationService.resyncConnectorRecords.called,
        'a refused request must not reach the resync',
      ).to.be.false;
      return next.firstCall.args[0];
    };

    it('refuses a second request while a sync is running', async () => {
      await refuses('SYNCING');
    });

    it('refuses one while a full sync is running', async () => {
      await refuses('FULL_SYNCING');
    });

    it('refuses one while a sync is already queued', async () => {
      // Added with queueing: the connector is not running, but a sync is owed,
      // so a second request would only be queued behind the first.
      const err = await refuses('QUEUED');
      expect(String(err?.message ?? '').toLowerCase()).to.contain('queued');
    });

    it('refuses one while the connector is locked', async () => {
      await refuses('IDLE', true);
    });

    it('lets an idle connector through to the resync', async () => {
      await run('IDLE');
      expect(next.called, String(next.firstCall?.args?.[0]?.message ?? '')).to
        .be.false;
      expect(recordRelationService.resyncConnectorRecords.calledOnce).to.be
        .true;
    });
  });
});
