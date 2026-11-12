import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as orgRepo from '../../../../../src/libs/repositories/org';
import * as cmConfig from '../../../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../../../src/libs/encryptor/encryptor';
import { WebSearchDuckDuckGoDefaultMigration } from '../../../../../src/modules/configuration_manager/services/migrations/web_search_duckduckgo_default.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';

const FLAG = configPaths.webSearchDuckDuckGoDefaultMigration;

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

// The store keeps the encrypted text; the fake cipher only prefixes it.
const encrypt = (value: unknown) => `enc:${JSON.stringify(value)}`;

const makeKv = (stored?: unknown) => {
  const values: Record<string, string | null> = {
    [configPaths.webSearch]: stored === undefined ? null : encrypt(stored),
  };
  return {
    values,
    get: sinon.stub().callsFake((path: string) => Promise.resolve(values[path] ?? null)),
    set: sinon.stub().callsFake((path: string, value: string) => {
      values[path] = value;
      return Promise.resolve();
    }),
    compareAndSet: sinon
      .stub()
      .callsFake((path: string, expected: string | null, value: string) => {
        if ((values[path] ?? null) !== expected) return Promise.resolve(false);
        values[path] = value;
        return Promise.resolve(true);
      }),
  };
};

const savedConfig = (kv: ReturnType<typeof makeKv>) =>
  JSON.parse((kv.values[configPaths.webSearch] as string).replace(/^enc:/, ''));

const provider = (name: string, isDefault: boolean) => ({
  provider: name,
  configuration: { apiKey: 'k' },
  providerKey: `key-${name}`,
  isDefault,
});

describe('WebSearchDuckDuckGoDefaultMigration', () => {
  let orgCount: sinon.SinonStub;

  beforeEach(() => {
    orgCount = sinon.stub().resolves(1);
    sinon.stub(orgRepo, 'getOrgRepository').returns({ count: orgCount } as any);
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'a', secretKey: 's' } as any);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (v: string) => `enc:${v}`,
      decrypt: (v: string) => v.replace(/^enc:/, ''),
    } as any);
  });

  afterEach(() => sinon.restore());

  const run = (kv: ReturnType<typeof makeKv>, logger = makeLogger()) =>
    new WebSearchDuckDuckGoDefaultMigration(logger as any, kv as any).run();

  it('stores DuckDuckGo as the default for an existing org that never chose a provider', async () => {
    const kv = makeKv();

    const result = await run(kv);

    expect(result).to.deep.equal({ seeded: true, errored: 0 });
    const providers = savedConfig(kv).providers;
    expect(providers).to.have.length(1);
    expect(providers[0]).to.include({ provider: 'duckduckgo', isDefault: true });
    expect(providers[0].providerKey).to.be.a('string');
    expect(kv.values[FLAG]).to.equal('true');
  });

  it('keeps the other stored providers and settings when none was the default', async () => {
    const kv = makeKv({
      providers: [provider('serper', false)],
      settings: { includeImages: true, maxImages: 5 },
    });

    await run(kv);

    const saved = savedConfig(kv);
    expect(saved.settings).to.deep.equal({ includeImages: true, maxImages: 5 });
    expect(saved.providers.map((p: any) => [p.provider, p.isDefault])).to.deep.equal([
      ['serper', false],
      ['duckduckgo', true],
    ]);
  });

  it('leaves an org that already chose a provider alone', async () => {
    const before = { providers: [provider('tavily', true)] };
    const kv = makeKv(before);

    const result = await run(kv);

    expect(result.seeded).to.equal(false);
    expect(savedConfig(kv)).to.deep.equal(before);
    expect(kv.compareAndSet.called).to.equal(false);
    expect(kv.values[FLAG]).to.equal('true');
  });

  it('does not seed a fresh install, but records that it looked', async () => {
    orgCount.resolves(0);
    const kv = makeKv();

    const result = await run(kv);

    expect(result).to.deep.equal({ seeded: false, errored: 0 });
    expect(kv.values[configPaths.webSearch]).to.equal(null);
    expect(kv.values[FLAG]).to.equal('true');
  });

  it('does nothing once it has run, so an admin who deletes DuckDuckGo keeps it deleted', async () => {
    const kv = makeKv();
    kv.values[FLAG] = 'true';

    const result = await run(kv);

    expect(result).to.deep.equal({ seeded: false, errored: 0 });
    expect(orgCount.called).to.equal(false);
    expect(kv.compareAndSet.called).to.equal(false);
  });

  it('retries on the next boot, without overwriting, when the stored settings cannot be read', async () => {
    const kv = makeKv();
    kv.values[configPaths.webSearch] = 'enc:not json';

    const result = await run(kv);

    expect(result.errored).to.equal(1);
    expect(kv.values[configPaths.webSearch]).to.equal('enc:not json');
    expect(kv.values[FLAG]).to.equal(undefined);
  });

  it('retries on the next boot when the store cannot be reached', async () => {
    const kv = makeKv();
    orgCount.rejects(new Error('db down'));

    const result = await run(kv);

    expect(result.errored).to.equal(1);
    expect(kv.values[FLAG]).to.equal(undefined);
  });

  it('re-reads and saves again when another replica wrote in between', async () => {
    const kv = makeKv({ providers: [] });
    let first = true;
    kv.compareAndSet.callsFake((path: string, expected: string | null, value: string) => {
      if (first) {
        first = false;
        kv.values[path] = encrypt({ providers: [provider('exa', false)] });
        return Promise.resolve(false);
      }
      if ((kv.values[path] ?? null) !== expected) return Promise.resolve(false);
      kv.values[path] = value;
      return Promise.resolve(true);
    });

    const result = await run(kv);

    expect(result.seeded).to.equal(true);
    expect(savedConfig(kv).providers.map((p: any) => p.provider)).to.deep.equal(['exa', 'duckduckgo']);
  });

  it('gives up for this boot, without the flag, when the settings keep changing', async () => {
    const kv = makeKv({ providers: [] });
    kv.compareAndSet.resolves(false);

    const result = await run(kv);

    expect(result.errored).to.equal(1);
    expect(kv.values[FLAG]).to.equal(undefined);
  }).timeout(5000);

  it('marks DuckDuckGo as the default when it was saved without being one', async () => {
    const kv = makeKv({ providers: [provider('duckduckgo', false)] });

    await run(kv);

    expect(savedConfig(kv).providers).to.have.length(1);
    expect(savedConfig(kv).providers[0].isDefault).to.equal(true);
  });
});
