import 'reflect-metadata';
import { expect } from 'chai';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import { AttemptKeyring, PublishedAttemptKeys } from '../../../src/modules/agent-harness/domain/authority';

describe('agent-harness gateway tokens (E2): provisioning through the service', () => {
  it('creates the gateway keyring once, publishes its public half, and rotates only on a new operator id', async () => {
    const signing = new InMemoryConfigDocumentStore<AttemptKeyring>();
    const published = new InMemoryConfigDocumentStore<PublishedAttemptKeys>();
    const service = new GatewayTokenService({
      tokens: new GatewayTokenAuthority({ signingKeys: signing, publishedKeys: published, clock: { now: () => 1_000 } }),
      runs: {} as never,
    });
    await service.provision(null);
    const first = (await signing.read()).value!;
    expect(first.keys).to.have.length(1);
    expect((await published.read()).value!.keys.map((key) => key.kid)).to.deep.equal([first.activeKid]);
    expect(JSON.stringify((await published.read()).value)).to.not.include(first.keys[0]!.privateKey);
    await service.provision(null);
    expect((await signing.read()).value!.activeKid).to.equal(first.activeKid);
    await service.provision('rotate-1');
    expect((await signing.read()).value!.activeKid).to.not.equal(first.activeKid);
  });
});
