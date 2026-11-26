/**
 * CPD-8 BB-1: the agent harness binds the binding service through its own
 * binder (WP-8), only when the deployment hands it the vault's KV store, and
 * keeps a service bound before it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { bindCredentialBindings } from '../../../src/modules/agent-harness/binders/credential-bindings';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { CONTROL_PLANE_BINDINGS } from '../../../src/modules/control-plane';
import { BindingService } from '../../../src/modules/control-plane/application/binding-service';
import { MemoryKv, platformCipher } from '../agent-harness/support/model-accounts';
import { instId, ORG_A } from './support/cp-objects';
import { testControlPlane } from './support/in-memory';
import { InMemoryBindingStore } from './support/in-memory-stores';

const SECRET = 'binder-bound-api-token-0123456789abcdef';

const context = (container: Container, credentialBindings?: unknown): HarnessBindingContext =>
  ({
    container,
    dependencies: credentialBindings === undefined ? {} : { credentialBindings },
    controlPlane: testControlPlane(),
  }) as unknown as HarnessBindingContext;

describe('CPD-8 credential-bindings binder (BB)', () => {
  it('BB-1: binds the service with the vault KV store, nothing without it, and keeps an earlier binding', () => {
    const bound = new Container();
    bindCredentialBindings(context(bound, { kv: new MemoryKv(), cipher: () => platformCipher }));
    expect(bound.get(CONTROL_PLANE_BINDINGS)).to.be.instanceOf(BindingService);

    const none = new Container();
    bindCredentialBindings(context(none));
    expect(none.isBound(CONTROL_PLANE_BINDINGS)).to.equal(false);

    const kept = new Container();
    const double = { double: true };
    kept.bind(CONTROL_PLANE_BINDINGS).toConstantValue(double);
    bindCredentialBindings(context(kept, { kv: new MemoryKv(), cipher: () => platformCipher }));
    expect(kept.get(CONTROL_PLANE_BINDINGS)).to.equal(double);
  });

  it('BB-2: the bound service writes through the deployment\'s transaction, vault and cipher', async () => {
    const container = new Container();
    const kv = new MemoryKv();
    bindCredentialBindings(context(container, { kv, cipher: () => platformCipher, bindings: new InMemoryBindingStore() }));
    const service = container.get<BindingService>(CONTROL_PLANE_BINDINGS);
    const request = {
      installation: instId(1),
      owner: 'user:u3' as const,
      mode: 'api_key' as const,
      kind: 'toolset',
      provider: null,
      material: { kind: 'record' as const, value: { apiToken: SECRET } },
      source: null,
    };
    const ctx = { orgId: ORG_A, actor: 'user:u3' };
    const first = await service.importRecord(ctx, request);
    const second = await service.importRecord(ctx, request);
    expect(first.binding?.id).to.match(/^bnd_[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(second.binding).to.deep.include({ id: first.binding!.id, revision: 2 });
    expect(second.binding!.secret!.version).to.equal(2);
    expect(await service.list(ORG_A, 'user:u3')).to.have.length(1);
    expect(await kv.dump()).to.not.include(SECRET);
  });
});
