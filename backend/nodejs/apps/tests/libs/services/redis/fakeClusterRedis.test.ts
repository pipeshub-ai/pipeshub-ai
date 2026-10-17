import { expect } from 'chai';

import {
  FakeClusterRedis,
  FakeClusterStore,
} from '../../../helpers/fakeClusterRedis';

async function expectRejection(
  fn: () => Promise<unknown>,
  matcher: RegExp,
): Promise<void> {
  try {
    await fn();
  } catch (error) {
    expect((error as Error).message).to.match(matcher);
    return;
  }
  expect.fail('expected promise to reject');
}

describe('FakeClusterRedis', () => {
  it('serves single-key commands normally', async () => {
    const fake = new FakeClusterRedis();
    await fake.set('a', '1');
    expect(await fake.get('a')).to.equal('1');
    expect(await fake.del('a')).to.equal(1);
    expect(await fake.get('a')).to.equal(null);
  });

  it('raises CROSSSLOT for a multi-key MGET spanning slots', async () => {
    const fake = new FakeClusterRedis();
    await fake.set('a', '1');
    await fake.set('b', '2');
    expect(fake.keySlot('a')).to.not.equal(fake.keySlot('b'));

    await expectRejection(() => fake.mget(['a', 'b']), /CROSSSLOT/);
  });

  it('raises CROSSSLOT for a multi-key DEL spanning slots', async () => {
    const fake = new FakeClusterRedis();
    await fake.set('a', '1');
    await fake.set('b', '2');

    await expectRejection(() => fake.del('a', 'b'), /CROSSSLOT/);
  });

  it('raises CROSSSLOT for a multi-key EXISTS spanning slots', async () => {
    const fake = new FakeClusterRedis();
    await fake.set('a', '1');
    await fake.set('b', '2');

    await expectRejection(() => fake.exists('a', 'b'), /CROSSSLOT/);
  });

  it('does not raise when every key hashes to the same slot ({hashtag})', async () => {
    const fake = new FakeClusterRedis();
    await fake.set('{crawling}:a', '1');
    await fake.set('{crawling}:b', '2');

    const values = await fake.mget(['{crawling}:a', '{crawling}:b']);
    expect(values).to.deep.equal(['1', '2']);
  });

  it('raises CROSSSLOT for EVAL/EVALSHA whose keys span slots', async () => {
    const fake = new FakeClusterRedis();
    const sha = await fake.script('LOAD', 'return 1');

    await expectRejection(
      () => fake.evalsha(sha, 2, 'a', 'b', 'ARGV_UNUSED'),
      /CROSSSLOT/,
    );
  });

  it('does not slot-check a non-transactional pipeline: each command routes independently', async () => {
    const fake = new FakeClusterRedis();
    await fake.set('a', '1');
    await fake.set('b', '2');

    const results = await fake
      .pipeline({ transaction: false })
      .get('a')
      .get('b')
      .exec();

    expect(results).to.deep.equal([
      [null, '1'],
      [null, '2'],
    ]);
  });

  it('shares data across instances backed by the same store, like two connections to one server', async () => {
    const store = new FakeClusterStore();
    const clientA = new FakeClusterRedis(store);
    const clientB = new FakeClusterRedis(store);

    await clientA.set('shared', 'value');

    expect(await clientB.get('shared')).to.equal('value');
  });

  it('rejects EVALSHA against an unloaded script with NOSCRIPT', async () => {
    const fake = new FakeClusterRedis();
    await expectRejection(() => fake.evalsha('deadbeef', 0), /NOSCRIPT/);
  });
});
