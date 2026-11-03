import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import winston from 'winston';
import * as edition from '../../../src/edition';
import * as seam from '../../../src/libs/services/structured-logging';
import { Logger } from '../../../src/libs/services/logger.service';
import { runWithRequestContext } from '../../../src/libs/context/request-context';

const { DefaultLoggingProvider, getProvider, resetProviderForTests } = seam;

function fakeProvider(overrides: Partial<seam.StructuredLoggingProvider> = {}): seam.StructuredLoggingProvider {
  return {
    consoleFormat: (f) => f,
    fileLoggingEnabled: () => true,
    hashOrgId: () => null,
    ...overrides,
  };
}

describe('libs/services/structured-logging', () => {
  beforeEach(() => resetProviderForTests());
  afterEach(() => {
    sinon.restore();
    resetProviderForTests();
    (Logger as any).instance = null;
  });

  describe('DefaultLoggingProvider', () => {
    it('returns the default console format', () => {
      const f = winston.format.simple();
      expect(new DefaultLoggingProvider().consoleFormat(f)).to.equal(f);
    });

    it('keeps file logging on', () => {
      expect(new DefaultLoggingProvider().fileLoggingEnabled()).to.equal(true);
    });

    it('leaves the org off the record', () => {
      expect(new DefaultLoggingProvider().hashOrgId('org-1')).to.equal(null);
    });
  });

  describe('getProvider', () => {
    it('uses the default when there is no override', () => {
      sinon.stub(edition, 'structuredLoggingProvider').returns(null);
      expect(getProvider()).to.be.instanceOf(DefaultLoggingProvider);
    });

    it('uses the override when one is supplied', () => {
      const custom = fakeProvider();
      sinon.stub(edition, 'structuredLoggingProvider').returns(custom);
      expect(getProvider()).to.equal(custom);
    });

    it('memoizes the provider', () => {
      const stub = sinon.stub(edition, 'structuredLoggingProvider').returns(null);
      const first = getProvider();
      expect(getProvider()).to.equal(first);
      expect(stub.callCount).to.equal(1);
    });

    it('rejects an override of the wrong shape', () => {
      sinon.stub(edition, 'structuredLoggingProvider').returns({ consoleFormat: () => null });
      expect(() => getProvider()).to.throw(TypeError, /StructuredLoggingProvider/);
    });
  });

  describe('Logger wiring', () => {
    function transportsOf(logger: Logger): winston.transport[] {
      return ((logger as any).logger as winston.Logger).transports;
    }

    it('drops the file transports when the provider says so', () => {
      sinon.stub(seam, 'getProvider').returns(fakeProvider({ fileLoggingEnabled: () => false }));
      const transports = transportsOf(new Logger({ service: 't' }));
      expect(transports).to.have.length(1);
      expect(transports[0]).to.be.instanceOf(winston.transports.Console);
    });

    it('keeps the file transports by default', () => {
      sinon.stub(seam, 'getProvider').returns(fakeProvider());
      const transports = transportsOf(new Logger({ service: 't' }));
      expect(transports.filter((t) => t instanceof winston.transports.File)).to.have.length(2);
    });

    it('gives the console transport the provider format', () => {
      const custom = winston.format.json();
      sinon.stub(seam, 'getProvider').returns(fakeProvider({ consoleFormat: () => custom }));
      const console = transportsOf(new Logger({ service: 't' })).find(
        (t) => t instanceof winston.transports.Console,
      );
      expect(console?.format).to.equal(custom);
    });

    it('stamps the provider org hash on records', () => {
      sinon.stub(seam, 'getProvider').returns(
        fakeProvider({ fileLoggingEnabled: () => false, hashOrgId: (o) => `h:${o}` }),
      );
      const logger = new Logger({ service: 't' });
      const log = sinon.stub((logger as any).logger, 'log');
      runWithRequestContext({ rootId: 'root-1', orgId: 'org-1' }, () => logger.info('m'));
      expect(log.firstCall.args[0].organization_id_hash).to.equal('h:org-1');
    });

    it('leaves the org off when the provider returns null', () => {
      sinon.stub(seam, 'getProvider').returns(fakeProvider({ fileLoggingEnabled: () => false }));
      const logger = new Logger({ service: 't' });
      const log = sinon.stub((logger as any).logger, 'log');
      runWithRequestContext({ rootId: 'root-1', orgId: 'org-1' }, () => logger.info('m'));
      expect(log.firstCall.args[0]).to.not.have.property('organization_id_hash');
    });
  });
});
