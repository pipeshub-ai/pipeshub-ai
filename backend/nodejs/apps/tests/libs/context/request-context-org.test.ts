import { expect } from 'chai';
import {
  ENVELOPE_ORG_ID,
  ENVELOPE_REQUEST_ID,
  contextFromEnvelope,
  getOrgId,
  getRequestContext,
  injectEnvelope,
  newSystemRoot,
  LogContextFields,
  bindLogContext,
  runWithRequestContext,
  setContextIdentity,
} from '../../../src/libs/context/request-context';

describe('request-context org and log context', () => {
  describe('setContextIdentity / getOrgId', () => {
    it('attaches the org and the user to the current context', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        setContextIdentity({ orgId: 'org-1', userId: 'user-1' });
        expect(getOrgId()).to.equal('org-1');
        expect(getRequestContext()).to.include({ orgId: 'org-1', userId: 'user-1' });
      });
    });

    it('is a no-op without a context', () => {
      setContextIdentity({ orgId: 'org-1', userId: 'user-1' });
      expect(getOrgId()).to.equal(undefined);
      expect(getRequestContext()).to.equal(undefined);
    });

    it('ignores empty and non-string values, each id on its own', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        for (const bad of ['', undefined, null, 42, { id: 'x' }]) {
          setContextIdentity({ orgId: bad, userId: bad });
        }
        expect(getRequestContext()).to.deep.equal({ rootId: 'root-1' });
        setContextIdentity({ orgId: 'org-1', userId: 7 });
        expect(getRequestContext()).to.deep.equal({ rootId: 'root-1', orgId: 'org-1' });
      });
    });

    it('sanitizes both ids', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        setContextIdentity({ orgId: 'org\r\n-1', userId: 'user <1>' });
        expect(getRequestContext()).to.include({ orgId: 'org-1', userId: 'user1' });
      });
    });
  });

  describe('bindLogContext', () => {
    it('merges ids across calls for the rest of the context', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        bindLogContext({ conversation_id: 'c1' });
        bindLogContext({ kb_id: 'kb1', record_id: 3 });
        bindLogContext({ kb_id: 'kb2' });
        expect(getRequestContext()?.logFields).to.deep.equal({ conversation_id: 'c1', kb_id: 'kb2', record_id: 3 });
      });
    });

    it('skips null and undefined values', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        bindLogContext({ kb_id: 'kb1' });
        bindLogContext({ kb_id: undefined, record_id: null as unknown as string });
        expect(getRequestContext()?.logFields).to.deep.equal({ kb_id: 'kb1' });
      });
    });

    it('drops keys outside LOG_CONTEXT_KEYS, the identity and record keys included', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        bindLogContext({
          kb_id: 'kb1',
          org_id: 'forged', user_id: 'forged', request_id: 'r', level: 'error', message: 'm', service: 's', metadata: 'x',
        } as unknown as LogContextFields);
        expect(getRequestContext()?.logFields).to.deep.equal({ kb_id: 'kb1' });
        bindLogContext({ level: 'error' } as unknown as LogContextFields);
        expect(getRequestContext()?.logFields).to.deep.equal({ kb_id: 'kb1' });
      });
    });

    it('is a no-op without a context', () => {
      expect(() => bindLogContext({ kb_id: 'kb1' })).not.to.throw();
      expect(getRequestContext()).to.equal(undefined);
    });

    it('keeps concurrent requests apart', async () => {
      const seen = await Promise.all(
        Array.from({ length: 50 }, (_, i) =>
          runWithRequestContext({ rootId: `root-${i}` }, async () => {
            bindLogContext({ kb_id: `kb-${i}` });
            await new Promise((r) => setImmediate(r));
            return getRequestContext()?.logFields?.kb_id === `kb-${i}`;
          }),
        ),
      );
      expect(seen.every(Boolean)).to.equal(true);
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
