/**
 * The harness stores that read main's people, organisations, chats and inbox, on PostgreSQL through main's
 * repositories (audit names, chat titles, the schedule actor authority): each answers only for the org it names.
 */
import 'reflect-metadata';
import { forEnumeratedOrg, orgContextStorage } from '../../../src/libs/context/orgContext';
import { UnboundOrgError } from '../../../src/libs/repositories/agent-harness/durable-transaction/bound-org';
import { randomUUID } from 'node:crypto';
import { expect } from 'chai';
import {
  PgChatTitles,
  PgMemberProfiles,
} from '../../../src/libs/repositories/agent-harness/audit-names/pg';
import { PgActorAuthority } from '../../../src/libs/repositories/agent-harness/actor-authority/pg';
import { newObjectId } from '../../../src/libs/repositories/ids';
import { setConversationRepository } from '../../../src/libs/repositories/conversation';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import { setNotificationRepository } from '../../../src/libs/repositories/notification';
import { PgNotificationRepository } from '../../../src/libs/repositories/notification/pg';
import { setOrgRepository } from '../../../src/libs/repositories/org';
import { PgOrgRepository } from '../../../src/libs/repositories/org/pg';
import { setUserRepository } from '../../../src/libs/repositories/user';
import { PgUserRepository } from '../../../src/libs/repositories/user/pg';
import {
  PgStack,
  pgBins,
  pgSuite,
  scopedStore,
  startHarnessPg,
} from './support/harness-pg';

for (const bin of pgBins()) {
  pgSuite(`harness people, chats and runs on PostgreSQL (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_directory');
      // What DOC_STORE=postgres picks: main's repositories on PostgreSQL.
      setUserRepository(new PgUserRepository());
      setOrgRepository(new PgOrgRepository());
      setNotificationRepository(new PgNotificationRepository());
      setConversationRepository(new PgConversationRepository());
    });
    after(async () => {
      setUserRepository();
      setOrgRepository();
      setNotificationRepository();
      setConversationRepository();
      await stack?.stop();
    });

    const newOrg = async (): Promise<string> => {
      const org = newObjectId();
      const now = new Date();
      await stack.admin((c) =>
        c.query(
          `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, registered_name, created_at, updated_at)
            VALUES ($1, $2, $3, 'business', 'Directory Org', $4, $4)`,
          [
            org,
            `${randomUUID().slice(0, 8)}.example.com`,
            `owner-${randomUUID().slice(0, 8)}@example.com`,
            now,
          ],
        ),
      );
      return org;
    };
    const newUser = async (
      org: string,
      fields: {
        name: string;
        role?: 'admin' | 'member';
        kind?: 'human' | 'service';
        disabled?: boolean;
        deleted?: boolean;
      },
    ): Promise<string> => {
      const id = newObjectId();
      const now = new Date();
      await stack.admin((c) =>
        c.query(
          `INSERT INTO identity.users (org_id, id, slug, full_name, email, role, kind, is_disabled, is_deleted, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)`,
          [
            org,
            id,
            `user-${id}`,
            fields.name,
            `${fields.name.toLowerCase().replace(/\W/g, '')}-${id}@example.com`,
            fields.role ?? 'member',
            fields.kind ?? 'human',
            fields.disabled ?? false,
            fields.deleted ?? false,
            now,
          ],
        ),
      );
      return id;
    };

    it("AUDITUI: names are the org's own live users only; titles are its chats that are not deleted", async () => {
      const [org, other] = [await newOrg(), await newOrg()];
      const ada = await newUser(org, { name: 'Ada Lovelace' });
      const gone = await newUser(org, { name: 'Gone User', deleted: true });
      const stranger = await newUser(other, { name: 'Stranger' });
      const profiles = await scopedStore(new PgMemberProfiles(), {
        profiles: 0,
      }).profiles(org, [ada, gone, stranger, 'not-an-id']);
      expect([...profiles.keys()]).to.deep.equal([ada]);
      expect(profiles.get(ada)?.name).to.equal('Ada Lovelace');
      const [kept, deleted, theirs] = [
        newObjectId(),
        newObjectId(),
        newObjectId(),
      ];
      await stack.admin(async (c) => {
        const now = new Date();
        const insert = (
          o: string,
          id: string,
          title: string,
          isDeleted: boolean,
        ) =>
          c.query(
            `INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, title, is_deleted, created_at, updated_at)
              VALUES ($1, $2, $3, $3, $4, $5, $6, $6)`,
            [o, id, ada, title, isDeleted, now],
          );
        await insert(org, kept, 'Quarterly numbers', false);
        await insert(org, deleted, 'Removed chat', true);
        await insert(other, theirs, 'Their chat', false);
      });
      const titles = await scopedStore(new PgChatTitles(), {
        titles: 0,
      }).titles(org, [kept, deleted, theirs]);
      expect([...titles.entries()]).to.deep.equal([
        [kept, 'Quarterly numbers'],
      ]);
    });

    it("P8a: a schedule's actor is authorised only as a live, enabled person of an active org", async () => {
      const [org, other] = [await newOrg(), await newOrg()];
      const person = await newUser(org, { name: 'Mia' });
      const disabled = await newUser(org, { name: 'Off', disabled: true });
      const bot = await newUser(org, { name: 'Bot', kind: 'service' });
      const removed = await newUser(org, { name: 'Left', deleted: true });
      const authority = scopedStore(new PgActorAuthority(() => 'test-secret'), {
        authorize: 0,
        isOrgAdmin: 0,
      });
      expect(await authority.authorize(org, person))
        .to.have.property('authorization')
        .that.matches(/^Bearer /);
      expect(await authority.authorize(org, disabled)).to.deep.equal({
        refused: 'actor_disabled',
      });
      expect(await authority.authorize(org, bot)).to.deep.equal({
        refused: 'actor_not_person',
      });
      expect(await authority.authorize(org, removed)).to.deep.equal({
        refused: 'actor_deleted',
      });
      expect(
        await scopedStore(new PgActorAuthority(() => 's'), {
          authorize: 0,
        }).authorize(other, person),
      ).to.deep.equal({
        refused: 'actor_deleted',
      });
    });

    it('with no organisation bound (the schedule firer) a call is refused; bound to the org of its row, it runs as that org', async () => {
      const [org, other] = [await newOrg(), await newOrg()];
      const admin = await newUser(org, { name: 'Ada', role: 'admin' });
      await newUser(other, { name: 'Other Admin', role: 'admin' });
      expect(
        await orgContextStorage.exit(() =>
          new PgActorAuthority(() => 's').isOrgAdmin(org, admin).catch((error: unknown) => error),
        ),
      ).to.be.instanceOf(UnboundOrgError);
      const as = <T>(orgId: string, fn: () => Promise<T>): Promise<T> =>
        forEnumeratedOrg(orgId, 'test-sweep', fn);
      expect(
        await as(org, () => new PgActorAuthority(() => 's').authorize(org, admin)),
      ).to.have.property('authorization');
      expect(
        await as(org, () => new PgActorAuthority(() => 's').isOrgAdmin(org, admin)),
      ).to.equal(true);
      expect(
        await as(other, () => new PgActorAuthority(() => 's').isOrgAdmin(other, admin)),
      ).to.equal(false);
      expect([
        ...(await as(org, () => new PgMemberProfiles().profiles(org, [admin]))).keys(),
      ]).to.deep.equal([admin]);
    });
  });
}
