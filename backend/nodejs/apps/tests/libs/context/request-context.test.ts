import { expect } from 'chai';
import {
  HEADER_REQUEST_ID,
  ENVELOPE_REQUEST_ID,
  NO_CONTEXT,
  sanitizeRootId,
  runWithRequestContext,
  getRequestContext,
  getRootId,
  newSystemRoot,
  newAnonRoot,
  currentDisplayId,
  injectRequestHeaders,
  injectEnvelope,
} from '../../../src/libs/context/request-context';

describe('request-context', () => {
  // ---------------------------------------------------------------------------
  // sanitizeRootId — client-controlled ids land verbatim in logs
  // ---------------------------------------------------------------------------
  describe('sanitizeRootId', () => {
    it('returns undefined for empty/nullish input', () => {
      expect(sanitizeRootId(undefined)).to.equal(undefined);
      expect(sanitizeRootId(null)).to.equal(undefined);
      expect(sanitizeRootId('')).to.equal(undefined);
    });

    it('preserves the allowed charset', () => {
      const raw = 'User_42.svc:abc-DEF';
      expect(sanitizeRootId(raw)).to.equal(raw);
    });

    it('strips CR/LF to prevent log-line forging', () => {
      expect(sanitizeRootId('abc\r\nFAKE LOG LINE')).to.equal('abcFAKELOGLINE');
    });

    it('strips other unsafe characters', () => {
      expect(sanitizeRootId('a b/c@d#e')).to.equal('abcde');
    });

    it('collapses an all-unsafe string to undefined', () => {
      expect(sanitizeRootId('@@@ ///')).to.equal(undefined);
    });

    it('truncates to the max length', () => {
      const out = sanitizeRootId('a'.repeat(250));
      expect(out).to.equal('a'.repeat(64));
    });

    it('preserves a full frontend id (<objectId>-<nanoid>) uncut', () => {
      // 24-char ObjectId + '-' + 21-char nanoid = 46 chars, under the 64 cap.
      const frontendId = '6a3992e0a771842adbf1039f-ZgUvzvsipDj0C_kjKwhMj';
      expect(frontendId.length).to.equal(46);
      expect(sanitizeRootId(frontendId)).to.equal(frontendId);
    });
  });

  // ---------------------------------------------------------------------------
  // AsyncLocalStorage binding
  // ---------------------------------------------------------------------------
  describe('runWithRequestContext / getRequestContext / getRootId', () => {
    it('returns undefined when called outside any context', () => {
      expect(getRequestContext()).to.equal(undefined);
      expect(getRootId()).to.equal(undefined);
    });

    it('binds the context for the duration of fn', () => {
      const result = runWithRequestContext({ rootId: 'root-1' }, () => {
        expect(getRequestContext()).to.deep.equal({ rootId: 'root-1' });
        expect(getRootId()).to.equal('root-1');
        return 'ok';
      });
      expect(result).to.equal('ok');
    });

    it('does not leak the context after fn returns', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => undefined);
      expect(getRootId()).to.equal(undefined);
    });

    it('propagates across an awaited async subtree', async () => {
      await runWithRequestContext({ rootId: 'root-async' }, async () => {
        await Promise.resolve();
        expect(getRootId()).to.equal('root-async');
      });
    });

    it('keeps nested contexts isolated', () => {
      runWithRequestContext({ rootId: 'outer' }, () => {
        runWithRequestContext({ rootId: 'inner' }, () => {
          expect(getRootId()).to.equal('inner');
        });
        expect(getRootId()).to.equal('outer');
      });
    });
  });

  // ---------------------------------------------------------------------------
  // newSystemRoot
  // ---------------------------------------------------------------------------
  describe('newSystemRoot', () => {
    it('mints a unique sys-prefixed id each call', () => {
      const a = newSystemRoot();
      const b = newSystemRoot();
      expect(a).to.not.equal(b);
      expect(a).to.match(/^sys-[a-f0-9]{32}$/);
    });
  });

  // ---------------------------------------------------------------------------
  // newAnonRoot
  // ---------------------------------------------------------------------------
  describe('newAnonRoot', () => {
    it('mints a unique anon-prefixed id matching Python new_anon_root', () => {
      const a = newAnonRoot();
      const b = newAnonRoot();
      expect(a).to.not.equal(b);
      expect(a).to.match(/^anon-[a-f0-9]{32}$/);
    });

    it('survives sanitizeRootId unchanged (safe charset, under cap)', () => {
      const id = newAnonRoot();
      expect(sanitizeRootId(id)).to.equal(id);
    });
  });

  // ---------------------------------------------------------------------------
  // currentDisplayId
  // ---------------------------------------------------------------------------
  describe('currentDisplayId', () => {
    it('returns the placeholder when there is no context', () => {
      expect(currentDisplayId()).to.equal(NO_CONTEXT);
    });

    it('returns the root id without a suffix', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        expect(currentDisplayId()).to.equal('root-1');
      });
    });

    it('appends the service suffix', () => {
      runWithRequestContext({ rootId: 'root-1' }, () => {
        expect(currentDisplayId('-node')).to.equal('root-1-node');
      });
    });
  });

  // ---------------------------------------------------------------------------
  // injectRequestHeaders — returns a copy, never mutates, never clobbers
  // ---------------------------------------------------------------------------
  describe('injectRequestHeaders', () => {
    it('returns a copy unchanged when there is no context', () => {
      const original = { 'content-type': 'application/json' };
      const out = injectRequestHeaders(original);
      expect(out).to.deep.equal(original);
      expect(out).to.not.equal(original);
    });

    it('defaults to an empty object', () => {
      expect(injectRequestHeaders()).to.deep.equal({});
    });

    it('adds the request id when a context is bound', () => {
      runWithRequestContext({ rootId: 'root-9' }, () => {
        const out = injectRequestHeaders({});
        expect(out[HEADER_REQUEST_ID]).to.equal('root-9');
      });
    });

    it('does not overwrite an existing header (case-insensitive)', () => {
      runWithRequestContext({ rootId: 'root-9' }, () => {
        const out = injectRequestHeaders({ 'X-Request-Id': 'preexisting' });
        expect(out['X-Request-Id']).to.equal('preexisting');
        expect(out[HEADER_REQUEST_ID]).to.equal(undefined);
      });
    });

    it('does not mutate the caller object', () => {
      runWithRequestContext({ rootId: 'root-9' }, () => {
        const original: Record<string, string> = {};
        injectRequestHeaders(original);
        expect(original).to.deep.equal({});
      });
    });
  });

  // ---------------------------------------------------------------------------
  // injectEnvelope — returns a copy, never mutates, never clobbers
  // ---------------------------------------------------------------------------
  describe('injectEnvelope', () => {
    it('returns the message unchanged when there is no context', () => {
      const msg = { recordId: 'r1' };
      const out = injectEnvelope(msg);
      expect(out).to.equal(msg);
    });

    it('adds the request id without mutating the input', () => {
      runWithRequestContext({ rootId: 'root-7' }, () => {
        const msg = { recordId: 'r1' };
        const out = injectEnvelope(msg);
        expect((out as Record<string, unknown>)[ENVELOPE_REQUEST_ID]).to.equal('root-7');
        expect(out).to.not.equal(msg);
        expect(msg).to.not.have.property(ENVELOPE_REQUEST_ID);
      });
    });

    it('does not overwrite an existing request id', () => {
      runWithRequestContext({ rootId: 'root-7' }, () => {
        const out = injectEnvelope({ [ENVELOPE_REQUEST_ID]: 'preexisting' });
        expect(out[ENVELOPE_REQUEST_ID]).to.equal('preexisting');
      });
    });
  });
});
