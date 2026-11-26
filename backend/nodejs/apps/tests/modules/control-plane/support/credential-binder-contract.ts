/**
 * CB and BV: what every `CredentialBinder` and `BindingStateView` do
 * (03-interfaces §5.2); CPD-8 runs it on `VaultCredentialBinder` over
 * `EncryptedConfigDocumentStore`.
 */
import { expect } from 'chai';
import {
  ControlPlaneRevisionConflictError,
  type ChangeEvent,
  type WriteContext,
} from '../../../../src/modules/control-plane';
import { ObjectExists, UnknownOAuthState } from '../../../../src/modules/control-plane/domain/store-errors';
import type { BindingStore } from '../../../../src/modules/control-plane/ports/binding-store';
import type {
  BindingStateView,
  CredentialBinder,
  SecretMaterial,
} from '../../../../src/modules/control-plane/ports/credential-binder';
import { assertNoCanary, VECTOR_EXAMPLES } from '../../agent-harness/support/canary-scan';
import { anInstallation, ORG_A, ORG_B, rejection } from './cp-objects';

export interface CredentialBinderWorld {
  readonly binder: CredentialBinder;
  readonly view: BindingStateView;
  readonly bindings: BindingStore;
  /** The secret the vault would hand the runtime for the binding's path; null when tombstoned or absent. */
  vaultRead(path: string): Promise<SecretMaterial | null>;
  /** The committed `config.changed` entries of the org, as stored. */
  configEntries(orgId: string): Promise<unknown[]>;
  /** The committed change events, in commit order. */
  changeEvents(): Promise<ChangeEvent[]>;
}

export interface CredentialBinderFixture {
  make(options?: { failingAudit?: boolean }): Promise<CredentialBinderWorld>;
}

const ctx: WriteContext = { orgId: ORG_A, actor: 'user:u3' };
const SECRET = VECTOR_EXAMPLES.find((example) => example.kind === 'github_token')!.secret;
const apiKey = (value = SECRET): SecretMaterial => ({ kind: 'api_key', value: { apiKey: value } });

export function describeCredentialBinderContract(name: string, fixture: CredentialBinderFixture): void {
  describe(`CredentialBinder contract (CB, BV): ${name}`, () => {
    it('CB-1: an API-key binding is active at secret version 1, and neither it nor its audit entry holds the key', async () => {
      const world = await fixture.make();
      const start = await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey());
      expect('authorizeUrl' in start).to.equal(false);
      const binding = start.binding;
      expect(binding).to.deep.include({ status: 'active', revision: 1 });
      expect(binding.secret?.version).to.equal(1);
      expect(await world.vaultRead(binding.secret!.path)).to.deep.equal(apiKey());
      assertNoCanary('binding', binding, { secrets: [SECRET], shapes: 'vendor' });
      assertNoCanary('stored binding', await world.bindings.get(ORG_A, binding.id), { secrets: [SECRET], shapes: 'vendor' });
      assertNoCanary('audit', await world.configEntries(ORG_A), { secrets: [SECRET], shapes: 'vendor' });
    });

    it('CB-2: an OAuth binding is pending until its state completes it, and a state works once', async () => {
      const world = await fixture.make();
      const start = await world.binder.begin(ctx, anInstallation(), 'user:u3', 'oauth');
      if (!('authorizeUrl' in start)) throw new Error('expected an authorize URL');
      expect(start.authorizeUrl).to.include(start.state);
      expect(start.binding).to.deep.include({ status: 'pending', secret: null });
      const tokens: SecretMaterial = { kind: 'oauth_tokens', value: { accessToken: SECRET }, expiresAt: 5000 };
      const done = await world.binder.complete(ctx, start.state, tokens);
      expect(done).to.deep.include({ id: start.binding.id, status: 'active', expiresAt: 5000 });
      expect(await rejection(world.binder.complete(ctx, start.state, tokens))).to.be.instanceOf(UnknownOAuthState);
      expect(await rejection(world.binder.complete(ctx, 'never-issued', tokens))).to.be.instanceOf(UnknownOAuthState);
      assertNoCanary('binding', done, { secrets: [SECRET], shapes: 'vendor' });
    });

    it('CB-3: setSecret writes a new version; a stale revision is a conflict and leaves the vault alone', async () => {
      const world = await fixture.make();
      const { binding } = await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey());
      const rotated = await world.binder.setSecret(ctx, binding.id, apiKey('rotated-key-1'), binding.revision);
      expect(rotated.secret?.version).to.equal(2);
      expect(rotated.secret?.versionDigest).to.not.equal(binding.secret?.versionDigest);
      expect(await world.vaultRead(rotated.secret!.path)).to.deep.equal(apiKey('rotated-key-1'));
      const stale = await rejection(world.binder.setSecret(ctx, binding.id, apiKey('rotated-key-2'), binding.revision));
      expect(stale).to.be.instanceOf(ControlPlaneRevisionConflictError);
      expect(await world.vaultRead(rotated.secret!.path)).to.deep.equal(apiKey('rotated-key-1'));
    });

    it('CB-4: revoke marks the binding revoked, tombstones the secret and is a narrowing', async () => {
      const world = await fixture.make();
      const { binding } = await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey());
      const revoked = await world.binder.revoke(ctx, binding.id, binding.revision);
      expect(revoked.status).to.equal('revoked');
      expect(await world.vaultRead(binding.secret!.path)).to.equal(null);
      const events = await world.changeEvents();
      expect(events.at(-1)).to.deep.include({ narrowing: true, touched: [{ kind: 'binding', id: binding.id }] });
      expect(events.slice(0, -1).map((event) => event.narrowing)).to.deep.equal([false]);
    });

    it('CB-6: an owner whose binding was revoked binds again: a new binding at a new secret version, the revoked one kept, its secret gone', async () => {
      const world = await fixture.make();
      const first = (await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey())).binding;
      await world.binder.revoke(ctx, first.id, first.revision);
      expect(await world.vaultRead(first.secret!.path)).to.equal(null);

      const again = (await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey('second-key-1'))).binding;
      expect(again.id).to.not.equal(first.id);
      expect(again).to.deep.include({ status: 'active', revision: 1 });
      expect(again.secret!.version).to.equal(first.secret!.version + 1);
      expect(again.secret!.versionDigest).to.not.equal(first.secret!.versionDigest);
      expect(await world.vaultRead(again.secret!.path)).to.deep.equal(apiKey('second-key-1'));
      expect(await world.bindings.get(ORG_A, first.id)).to.deep.include({ status: 'revoked' });
      expect(await world.bindings.find(ORG_A, anInstallation().id, 'user:u3')).to.deep.include({ id: again.id });
      // A live binding still refuses a second one.
      expect(await rejection(world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey('third-key-1')))).to.be.instanceOf(ObjectExists);
    });

    it('CB-5: every call is one audited write, and an audit failure leaves binding and vault unchanged', async () => {
      const world = await fixture.make();
      const { binding } = await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey());
      await world.binder.setSecret(ctx, binding.id, apiKey('rotated-key-1'), 1);
      expect(await world.configEntries(ORG_A)).to.have.length(2);
      const broken = await fixture.make({ failingAudit: true });
      expect(await rejection(broken.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey()))).to.be.an('error');
      expect(await broken.bindings.listForOwner(ORG_A, 'user:u3')).to.deep.equal([]);
      expect(await broken.vaultRead(binding.secret!.path)).to.equal(null);
    });

    it('BV-1: the view shows status and expiry only, and never another org\'s binding', async () => {
      const world = await fixture.make();
      const { binding } = await world.binder.begin(ctx, anInstallation(), 'user:u3', 'api_key', apiKey());
      const summary = await world.view.state(ORG_A, binding.id);
      expect(summary).to.deep.equal({
        id: binding.id,
        installation: binding.installation,
        owner: 'user:u3',
        mode: 'api_key',
        status: 'active',
        expiresAt: null,
        refreshedAt: binding.refreshedAt,
      });
      expect(await world.view.state(ORG_B, binding.id)).to.equal(null);
    });
  });
}
