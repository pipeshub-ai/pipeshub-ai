import { expect } from 'chai';
import {
  ENVELOPE_ORG_ID,
  ENVELOPE_REQUEST_ID,
  contextFromEnvelope,
  getOrgId,
  getRequestContext,
  injectEnvelope,
  newSystemRoot,
  runWithRequestContext,
  setContextOrgId,
} from '../../../src/libs/context/request-context';

describe('request-context org', () => {
  describe('setContextOrgId / getOrgId', () => {
    it('attaches the org to the current context', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        setContextOrgId('org-1');
        expect(getOrgId()).to.equal('org-1');
        expect(getRequestContext()?.orgId).to.equal('org-1');
      });
    });

    it('is a no-op without a context', () => {
      setContextOrgId('org-1');
      expect(getOrgId()).to.equal(undefined);
    });

    it('ignores empty and non-string values', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        setContextOrgId('');
        setContextOrgId(undefined);
        setContextOrgId(null);
        setContextOrgId(42);
        setContextOrgId({ id: 'org-1' });
        expect(getOrgId()).to.equal(undefined);
      });
    });

    it('sanitizes the org id', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        setContextOrgId('org\r\n-1');
        expect(getOrgId()).to.equal('org-1');
      });
    });
  });

  describe('injectEnvelope', () => {
    it('stamps the org when the context has one', () => {
      runWithRequestContext({ rootId: 'root-7', orgId: 'org-7' }, () => {
        const out = injectEnvelope({ recordId: 'r1' }) as Record<string, unknown>;
        expect(out[ENVELOPE_REQUEST_ID]).to.equal('root-7');
        expect(out[ENVELOPE_ORG_ID]).to.equal('org-7');
      });
    });

    it('leaves the org out when the context has none', () => {
      runWithRequestContext({ rootId: 'root-7' }, () => {
        const out = injectEnvelope({ recordId: 'r1' }) as Record<string, unknown>;
        expect(out).to.not.have.property(ENVELOPE_ORG_ID);
      });
    });

    it('keeps an org the producer already set', () => {
      runWithRequestContext({ rootId: 'root-7', orgId: 'org-7' }, () => {
        const out = injectEnvelope({ [ENVELOPE_ORG_ID]: 'org-producer' }) as Record<string, unknown>;
        expect(out[ENVELOPE_ORG_ID]).to.equal('org-producer');
      });
    });

    it('does not mutate the input', () => {
      const msg = { recordId: 'r1' };
      runWithRequestContext({ rootId: 'root-7', orgId: 'org-7' }, () => {
        injectEnvelope(msg);
      });
      expect(msg).to.deep.equal({ recordId: 'r1' });
    });
  });

  describe('contextFromEnvelope', () => {
    it('reads root id and org', () => {
      const ctx = contextFromEnvelope({ [ENVELOPE_REQUEST_ID]: 'root-1', [ENVELOPE_ORG_ID]: 'org-1' });
      expect(ctx).to.deep.equal({ rootId: 'root-1', orgId: 'org-1' });
    });

    it('omits the org when absent', () => {
      const ctx = contextFromEnvelope({ [ENVELOPE_REQUEST_ID]: 'root-1' });
      expect(ctx).to.deep.equal({ rootId: 'root-1' });
    });

    it('falls back to an anon root without a request id', () => {
      expect(contextFromEnvelope(undefined).rootId).to.match(/^anon-/);
      expect(contextFromEnvelope({}).rootId).to.match(/^anon-/);
    });

    it('uses the given root factory without a request id', () => {
      expect(contextFromEnvelope({}, newSystemRoot).rootId).to.match(/^sys-/);
    });

    it('ignores non-string ids instead of throwing', () => {
      const ctx = contextFromEnvelope({ [ENVELOPE_REQUEST_ID]: 7, [ENVELOPE_ORG_ID]: {} });
      expect(ctx.rootId).to.match(/^anon-/);
      expect(ctx).to.not.have.property('orgId');
    });
  });
});
