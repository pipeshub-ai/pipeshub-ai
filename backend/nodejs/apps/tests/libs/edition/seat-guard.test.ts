import { expect } from 'chai';
import { noopEditionBoot } from '../../../src/libs/edition/edition-boot';
import {
  assertSeatFor,
  getSeatGuard,
  noopSeatGuard,
  setSeatGuard,
} from '../../../src/libs/edition/seat-guard';
import { ForbiddenError } from '../../../src/libs/errors/http.errors';

describe('seat guard seam', () => {
  afterEach(() => setSeatGuard());

  it('defaults to the no-op guard, which is also the no-op edition boot’s', () => {
    expect(getSeatGuard()).to.equal(noopSeatGuard);
    expect(noopEditionBoot.seatGuard).to.equal(noopSeatGuard);
  });

  it('the no-op guard allows everything', async () => {
    expect((await noopSeatGuard.check()).room).to.equal(Infinity);
    await assertSeatFor();
    await assertSeatFor('signIn');
    await noopSeatGuard.afterChange();
  });

  it('refuses with the words for the audience when the edition has no room', async () => {
    setSeatGuard({
      check: async () => ({
        room: 0,
        refusal: { admin: 'full (admin)', signIn: 'full (sign-in)' },
      }),
      afterChange: async () => {},
    });
    for (const [audience, words] of [
      ['admin', 'full (admin)'],
      ['signIn', 'full (sign-in)'],
    ] as const) {
      let error: unknown;
      try {
        await assertSeatFor(audience);
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(ForbiddenError);
      expect((error as Error).message).to.equal(words);
    }
  });
});
