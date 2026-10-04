import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { SamlDesktopHandoffService } from '../../../../src/modules/auth/services/samlDesktopHandoff.service';

// RFC 7636 appendix B.
const VERIFIER = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk';
const CHALLENGE = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM';

async function expectRejected(promise: Promise<unknown>): Promise<void> {
  try {
    await promise;
  } catch (error) {
    expect((error as Error).message).to.equal('Invalid or expired sign-in code');
    return;
  }
  expect.fail('expected rejection');
}

describe('SamlDesktopHandoffService', () => {
  let store: Map<string, unknown>;
  let service: SamlDesktopHandoffService;
  let set: sinon.SinonStub;

  beforeEach(() => {
    store = new Map();
    set = sinon.stub().callsFake(async (k: string, v: unknown) => {
      store.set(k, v);
    });
    service = new SamlDesktopHandoffService({
      set,
      get: sinon.stub().callsFake(async (k: string) => store.get(k) ?? null),
      delete: sinon.stub().callsFake(async (k: string) => {
        store.delete(k);
      }),
      increment: sinon.stub(),
      disconnect: sinon.stub(),
      isConnected: () => true,
    });
  });

  afterEach(() => sinon.restore());

  it('stores the tokens under a short-lived code', async () => {
    const code = await service.issue({ accessToken: 'at', refreshToken: 'rt' }, CHALLENGE);

    expect(code).to.match(/^[0-9a-f]{64}$/);
    expect(set.firstCall.args[2]).to.deep.equal({ ttl: 120 });
  });

  it('redeems once with the matching verifier', async () => {
    const code = await service.issue({ accessToken: 'at', refreshToken: 'rt' }, CHALLENGE);

    expect(await service.redeem(code, VERIFIER)).to.deep.equal({ accessToken: 'at', refreshToken: 'rt' });
    await expectRejected(service.redeem(code, VERIFIER));
  });

  it('burns the code on a wrong verifier', async () => {
    const code = await service.issue({ accessToken: 'at', refreshToken: 'rt' }, CHALLENGE);

    await expectRejected(service.redeem(code, 'x'.repeat(43)));
    await expectRejected(service.redeem(code, VERIFIER));
  });

  it('rejects an unknown or expired code', async () => {
    await expectRejected(service.redeem('0'.repeat(64), VERIFIER));
  });
});
