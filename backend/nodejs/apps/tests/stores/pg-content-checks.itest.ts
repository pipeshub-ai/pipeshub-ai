/**
 * What only PostgreSQL has to prove for the document, project, connectors-config, Slack thread and mail log stores: row policies,
 * the idempotency key under a race, the single winner of an upload lease, path ranges, the connector tag kept in step, member
 * order and versions under concurrency, and where a mail log row lands among the month partitions.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  orgContextStorage,
} from '../../src/libs/context/orgContext';
import { InternalServerError } from '../../src/libs/errors/http.errors';
import { PgConnectorsConfigRepository } from '../../src/libs/repositories/connectors-config/pg';
import { PgDocumentRepository } from '../../src/libs/repositories/document/pg';
import { PgProjectRepository } from '../../src/libs/repositories/project/pg';
import { PgSlackThreadRepository } from '../../src/libs/repositories/slack-thread/pg';
import { setIdentityPort } from '../../src/libs/repositories/identity';
import { PgIdentityPort } from '../../src/libs/repositories/identity/pg';
import { PgMailLogRepository } from '../../src/libs/repositories/mail-log/pg';
import { PgUnitOfWork, kernelTx } from '../../src/libs/repositories/pg';
import { StorageVendor } from '../../src/modules/storage/types/storage.service.types';
import type { NewDocument } from '../../src/libs/repositories/document';
import {
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;
const doc = (orgId: string, over: Partial<NewDocument> = {}): NewDocument => ({
  orgId,
  initiatorUserId: null,
  documentName: `d-${newId()}`,
  isVersionedFile: false,
  storageVendor: StorageVendor.Local,
  extension: '.txt',
  ...over,
});
const outsideAnyOrg = <T>(fn: () => T): T => orgContextStorage.exit(fn);

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL content checks (${bin})`, function () {
    this.timeout(240_000);
    let stack: PgStack;
    const docs = new PgDocumentRepository();
    const projects = new PgProjectRepository();
    const connectors = new PgConnectorsConfigRepository();
    const slack = new PgSlackThreadRepository();
    const INSTALL_ORG = newId();
    const mail = new PgMailLogRepository();

    before(async () => {
      stack = await startPgStack(bin, 'content_checks', {
        pool: 4,
        clientPool: 24,
      });
      // the install's organisation: the one a call with none in context runs as
      await stack.admin((c) =>
        c.query(
          `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, created_at, updated_at)
           VALUES ($1, 'example.com', 'a@example.com', 'business', now(), now())`,
          [INSTALL_ORG],
        ),
      );
      setIdentityPort(new PgIdentityPort());
    });
    after(async () => {
      setIdentityPort();
      await stack?.stop();
    });

    /** A statement run as an organisation through the application login, row policies in force. */
    const asOrg = <T = Record<string, unknown>>(
      org: string,
      text: string,
      values: unknown[] = [],
    ): Promise<T[]> =>
      inOrg(org, () =>
        new PgUnitOfWork().run(
          async (tx) => (await kernelTx(tx).query<T>(text, values)).rows,
        ),
      );
    const count = async (org: string, table: string) =>
      Number(
        (
          await asOrg<{ n: string }>(
            org,
            `/* pipeshub:test.count */ SELECT count(*) AS n FROM ${table}`,
          )
        )[0]?.n,
      );

    describe('row-level security', () => {
      it('each table shows an organisation only its own rows, and refuses a row written for another', async () => {
        const a = newId(),
          b = newId();
        await inOrg(a, async () => {
          await docs.create(doc(a));
          await mail.record({
            orgId: a,
            from: 'f@x.io',
            to: ['t@x.io'],
            emailTemplateType: 'x',
          });
        });
        const tables = ['content.documents', 'platform.mail_log'];
        for (const t of tables) {
          expect(await count(a, t), `${t} for its org`).to.be.greaterThan(0);
          expect(await count(b, t), `${t} for another`).to.equal(0);
        }
        await inOrg(b, async () => {
          for (const t of ['content.documents']) {
            const cols: Record<string, string> = {
              'content.documents':
                "(org_id, id, document_name, storage_vendor, created_at, updated_at) VALUES ($1, '0000000000000000000000f1', 'x', 'local', now(), now())",
            };
            let denied: unknown;
            try {
              await new PgUnitOfWork().run(
                async (tx) => { await kernelTx(tx).query(
                    `/* pipeshub:test.cross */ INSERT INTO ${t} ${cols[t]}`,
                    [a],
                  ) },
              );
            } catch (e) {
              denied = e;
            }
            expect(denied, t).to.be.instanceOf(InternalServerError);
          }
        });
      });

      it('a foreign id is not found, changed or removed; a call that names another organisation is refused', async () => {
        const a = newId(),
          b = newId();
        const d = await inOrg(a, () => docs.create(doc(a)));
        await inOrg(b, async () => {
          expect(await docs.get(b, d._id)).to.equal(null);
          expect(await docs.update(b, d._id, { mutationCount: 9 })).to.equal(
            null,
          );
          await docs.remove(d._id);
          await docs.setFields(d._id, { documentPath: 'x' });
          expect(
            await docs.claimUpload(d._id, StorageVendor.Local, 'x', 1000),
          ).to.equal(null);
          let named: unknown;
          try {
            await docs.get(a, d._id);
          } catch (e) {
            named = e;
          }
          expect(named).to.be.instanceOf(InternalServerError);
        });
        expect(
          (await inOrg(a, () => docs.get(a, d._id)))?.mutationCount,
        ).to.equal(1);
      });
    });

    describe('documents', () => {
      it('names, paths and tags are trimmed, storage info keeps its own fields, and an initiator that was never given stays absent', async () => {
        const org = newId();
        const bare = doc(org, {
          documentName: '  padded  ',
          alternateDocumentName: ' alt ',
          documentPath: ' p/q ',
          tags: [' a ', 'b'],
          local: { url: 'u', localPath: 'l', stray: 1 } as never,
        });
        delete bare.initiatorUserId; // an initiator that was never given
        const made = await inOrg(org, () => docs.create(bare));
        expect(made).to.include({
          documentName: 'padded',
          alternateDocumentName: 'alt',
          documentPath: 'p/q',
        });
        expect(made.tags).to.deep.equal(['a', 'b']);
        expect(made.local).to.deep.equal({ url: 'u', localPath: 'l' });
        expect('initiatorUserId' in made).to.equal(false);
        const given = await inOrg(org, () =>
          docs.create(doc(org, { initiatorUserId: null })),
        );
        expect(given.initiatorUserId).to.equal(null);
        const moved = await inOrg(org, () =>
          docs.update(org, made._id, {
            documentPath: ' x/y ',
            s3: { url: 's', other: 1 } as never,
          }),
        );
        expect(moved?.documentPath).to.equal('x/y');
        expect(moved?.s3).to.deep.equal({ url: 's' });
        expect(await inOrg(org, () => docs.subtree(org, 'x/y'))).to.have.length(
          1,
        );
      });

      it('a version entry and a custom metadata entry keep only the fields the document schema names', async () => {
        const org = newId();
        const made = await inOrg(org, () =>
          docs.create(
            doc(org, {
              customMetadata: [{ key: 'k', value: 1, stray: true } as never],
            }),
          ),
        );
        const after = await inOrg(org, () =>
          docs.update(
            org,
            made._id,
            {},
            {
              pushVersions: [
                {
                  version: 1,
                  size: 3,
                  mutationCount: 2,
                  local: { url: 'u', other: 1 },
                } as never,
              ],
            },
          ),
        );
        expect(after?.versionHistory).to.deep.equal([
          { version: 1, size: 3, local: { url: 'u' } },
        ]);
        expect(after?.customMetadata).to.deep.equal([{ key: 'k', value: 1 }]);
      });

      it('twenty callers with one key: one creates, nineteen get its row; the database holds one', async () => {
        const org = newId(),
          user = newId();
        const results = await Promise.all(
          Array.from({ length: 20 }, () =>
            inOrg(org, () =>
              docs.createIdempotent(
                doc(org, { initiatorUserId: user, documentName: 'one' }),
                { key: 'k', fingerprint: 'f' },
                {},
              ),
            ),
          ),
        );
        expect(results.filter((r) => !r.replayed)).to.have.length(1);
        expect(new Set(results.map((r) => r.document._id)).size).to.equal(1);
        expect(
          await asOrg(
            org,
            "/* pipeshub:test.keyed */ SELECT 1 FROM content.documents WHERE idempotency_key = 'k'",
          ),
        ).to.have.length(1);
      });

      it('a service token (no initiator) keys like any principal, in the database too', async () => {
        const org = newId();
        const first = await inOrg(org, () =>
          docs.createIdempotent(doc(org), { key: 'svc', fingerprint: 'f' }, {}),
        );
        const again = await inOrg(org, () =>
          docs.createIdempotent(doc(org), { key: 'svc', fingerprint: 'f' }, {}),
        );
        expect(first.document.initiatorUserId).to.equal(null);
        expect(again).to.deep.include({ replayed: true });
        let dup: unknown;
        try {
          await stack.admin((c) =>
            c.query(
              `INSERT INTO content.documents (org_id, id, document_name, storage_vendor, idempotency_key, created_at, updated_at)
                 VALUES ($1, '0000000000000000000000f2', 'x', 'local', 'svc', now(), now())`,
              [org],
            ),
          );
        } catch (e) {
          dup = e;
        }
        expect((dup as { code?: string }).code).to.equal('23505');
      });

      it('of ten retries claiming one unfinished upload, one wins', async () => {
        const org = newId();
        const d = await inOrg(org, () => docs.create(doc(org)));
        const got = await Promise.all(
          Array.from({ length: 10 }, (_, i) =>
            inOrg(org, () =>
              docs.claimUpload(d._id, StorageVendor.Local, `t${i}`, 60_000),
            ),
          ),
        );
        expect(got.filter(Boolean)).to.have.length(1);
      });

      it('subtree and rewriteTree move whole path segments only, in byte order', async () => {
        const org = newId();
        await inOrg(org, async () => {
          const at = await docs.create(
            doc(org, { documentPath: `${org}/Pipes/a b` }),
          );
          const below = await docs.create(
            doc(org, { documentPath: `${org}/Pipes/a b/é/Z` }),
          );
          const siblings = [
            `${org}/Pipes/a b0`,
            `${org}/Pipes/a b-`,
            `${org}/Pipes/a bc/x`,
            `${org}/Pipes/A b/x`,
          ];
          const others = await Promise.all(
            siblings.map((p) => docs.create(doc(org, { documentPath: p }))),
          );
          expect(
            (await docs.subtree(org, `${org}/Pipes/a b`))
              .map((r) => r._id)
              .sort(),
          ).to.deep.equal([at._id, below._id].sort());
          await docs.rewriteTree(`${org}/Pipes/a b`, `${org}/Pipes/moved`, [
            { id: at._id, set: {} },
            { id: below._id, set: {} },
          ]);
          expect((await docs.get(org, below._id))?.documentPath).to.equal(
            `${org}/Pipes/moved/é/Z`,
          );
          for (const [i, o] of others.entries())
            expect((await docs.get(org, o._id))?.documentPath).to.equal(
              siblings[i],
            );
        });
      });

      it('a tree rewrite is one transaction: a row that cannot be written leaves every path as it was', async () => {
        const org = newId();
        await inOrg(org, async () => {
          const a = await docs.create(doc(org, { documentPath: `${org}/t/a` }));
          const b = await docs.create(doc(org, { documentPath: `${org}/t/b` }));
          let failed: unknown;
          try {
            await docs.rewriteTree(`${org}/t`, `${org}/u`, [
              { id: a._id, set: {} },
              { id: b._id, set: { notAField: 1 } },
            ]);
          } catch (e) {
            failed = e;
          }
          expect(failed).to.be.instanceOf(InternalServerError);
          expect((await docs.get(org, a._id))?.documentPath).to.equal(
            `${org}/t/a`,
          );
          expect((await docs.get(org, b._id))?.documentPath).to.equal(
            `${org}/t/b`,
          );
        });
      });

      it('the connector tag column follows customMetadata on create, setFields and the relocation path', async () => {
        const org = newId();
        const tagOf = async (id: string) =>
          (
            await asOrg<{ connector_id: string | null }>(
              org,
              '/* pipeshub:test.tag */ SELECT connector_id FROM content.documents WHERE id = $1',
              [id],
            )
          )[0]?.connector_id;
        await inOrg(org, async () => {
          const d = await docs.create(
            doc(org, {
              customMetadata: [
                { key: 'connectorId', value: 'c1' },
                { key: 'x', value: 1 },
              ],
            }),
          );
          expect(await tagOf(d._id)).to.equal('c1');
          await docs.setFields(d._id, {
            customMetadata: [{ key: 'connectorId', value: 'c2' }],
          });
          expect(await tagOf(d._id)).to.equal('c2');
          await docs.setFields(d._id, {
            customMetadata: [{ key: 'other', value: 'c2' }],
          });
          expect(await tagOf(d._id)).to.equal(null);
          const plain = await docs.create(doc(org));
          await docs.rewriteTree(`${org}/x`, `${org}/y`, [
            {
              id: plain._id,
              set: { customMetadata: [{ key: 'connectorId', value: 'c3' }] },
            },
          ]);
          expect(await tagOf(plain._id)).to.equal('c3');
          expect(
            await docs.pageByConnector(
              org,
              { connectorId: 'c3', prefixes: [] },
              undefined,
              5,
            ),
          ).to.have.length(1);
        });
      });

      it('setFields takes the dotted names the storage code uses and refuses any other', async () => {
        const org = newId();
        await inOrg(org, async () => {
          const d = await docs.create(
            doc(org, { s3: { url: 'u' }, storageVendor: StorageVendor.S3 }),
          );
          await docs.setFields(d._id, { 's3.url': 'v', 'azureBlob.url': 'w' });
          const got = await docs.get(org, d._id);
          expect(got?.s3).to.deep.equal({ url: 'v' });
          expect(got?.azureBlob).to.deep.equal({ url: 'w' });
          let refused: unknown;
          try {
            await docs.setFields(d._id, { 's3.bucket': 'x' });
          } catch (e) {
            refused = e;
          }
          expect(refused).to.be.instanceOf(InternalServerError);
        });
      });

      it('streams tagged rows filed outside the prefixes in id order', async () => {
        const org = newId();
        const ids = await inOrg(org, async () => {
          const made: string[] = [];
          for (let i = 0; i < 12; i++)
            made.push(
              (
                await docs.create(
                  doc(org, {
                    customMetadata: [{ key: 'connectorId', value: 'big' }],
                  }),
                )
              )._id,
            );
          return made;
        });
        const seen: string[] = [];
        await inOrg(org, async () => {
          for await (const d of docs.streamTaggedOutsidePrefixes(org, {
            connectorId: 'big',
            prefixes: ['nowhere'],
          }))
            seen.push(d._id);
        });
        expect(seen).to.deep.equal([...ids].sort());
      });
    });

    describe('projects', () => {
      it('ten people added at once: all ten members, ten distinct positions, __v moved by ten', async () => {
        const org = newId(),
          owner = newId();
        const p = await inOrg(org, () =>
          projects.create(org, owner, { name: 'crowd', tools: [] }),
        );
        const people = Array.from({ length: 10 }, () => newId());
        await Promise.all(
          people.map((id) =>
            inOrg(org, () =>
              projects.upsertMembers(
                p._id,
                [{ principalId: id, principalType: 'user', role: 'viewer' }],
                owner,
              ),
            ),
          ),
        );
        const got = await inOrg(org, () => projects.getLive(p._id));
        expect(got?.members.map((m) => m.principalId).sort()).to.deep.equal(
          [...people].sort(),
        );
        expect(got?.__v).to.equal(10);
        const pos = await asOrg<{ pos: number }>(
          org,
          '/* pipeshub:test.pos */ SELECT pos FROM content.project_members WHERE project_id = $1',
          [p._id],
        );
        expect(new Set(pos.map((r) => r.pos)).size).to.equal(10);
      });

      it('__v moves when a member is added or removed, not when a role changes; members keep the order they were added in', async () => {
        const org = newId(),
          owner = newId(),
          u1 = newId(),
          u2 = newId(),
          t1 = newId();
        await inOrg(org, async () => {
          const p = await projects.create(org, owner, { name: 'v', tools: [] });
          expect(p.__v).to.equal(0);
          await projects.upsertMembers(
            p._id,
            [
              { principalId: u1, principalType: 'user', role: 'viewer' },
              { principalId: t1, principalType: 'team', role: 'viewer' },
            ],
            owner,
          );
          const after = await projects.upsertMembers(
            p._id,
            [{ principalId: u2, principalType: 'user', role: 'editor' }],
            owner,
          );
          expect(after?.members.map((m) => m.principalId)).to.deep.equal([
            u1,
            t1,
            u2,
          ]);
          expect(after?.__v).to.equal(2);
          expect(
            (
              await projects.upsertMembers(
                p._id,
                [{ principalId: u1, principalType: 'user', role: 'editor' }],
                owner,
              )
            )?.__v,
          ).to.equal(2);
          expect(
            (await projects.removeMember(p._id, u1, 'user'))?.__v,
          ).to.equal(3);
          expect(
            (await projects.removeMember(p._id, u1, 'user'))?.__v,
          ).to.equal(3);
          expect(
            (await projects.getLive(p._id))?.members.map((m) => m.principalId),
          ).to.deep.equal([t1, u2]);
        });
      });

      it('lists pinned first, then newest activity, then id; the search treats % and _ as text; the total ignores the page', async () => {
        const org = newId(),
          me = newId();
        await inOrg(org, async () => {
          const names = ['a_b', 'axb', '100%', '100 percent'];
          const made = [];
          for (const name of names) {
            made.push(await projects.create(org, me, { name, tools: [] }));
            await new Promise((r) => setTimeout(r, 3));
          }
          await projects.setPinned(made[0]!._id, me, true, false);
          const q = {
            scope: 'mine' as const,
            includeArchived: false,
            teamIds: [],
            skip: 0,
            limit: 50,
          };
          expect(
            (await projects.list(org, me, q)).rows.map((r) => r.name),
          ).to.deep.equal(['a_b', '100 percent', '100%', 'axb']);
          expect(
            (await projects.list(org, me, { ...q, search: 'a_b' })).rows.map(
              (r) => r.name,
            ),
          ).to.deep.equal(['a_b']);
          expect(
            (await projects.list(org, me, { ...q, search: '100%' })).rows.map(
              (r) => r.name,
            ),
          ).to.deep.equal(['100%']);
          const page = await projects.list(org, me, {
            ...q,
            skip: 3,
            limit: 2,
          });
          expect(page.rows.map((r) => r.name)).to.deep.equal(['axb']);
          expect(page.total).to.equal(4);
        });
      });

      it('a legacy project-wide pin counts as the owner\'s pin only', async () => {
        const org = newId(),
          owner = newId(),
          member = newId();
        await inOrg(org, async () => {
          const old = await projects.create(org, owner, { name: 'Old', tools: [] });
          await new Promise((r) => setTimeout(r, 3));
          await projects.create(org, owner, { name: 'New', tools: [] });
          await projects.upsertMembers(
            old._id,
            [{ principalId: member, principalType: 'user', role: 'viewer' }],
            owner,
          );
          await stack.admin((c) =>
            c.query(
              'UPDATE content.projects SET is_pinned = true, last_activity_at = now() - interval \'1 day\' WHERE id = $1',
              [old._id],
            ),
          );
          const q = {
            scope: 'all' as const,
            includeArchived: false,
            teamIds: [],
            skip: 0,
            limit: 10,
          };
          const names = async (who: string) =>
            (await projects.list(org, who, q)).rows.map((r) => r.name);
          expect(await names(owner)).to.deep.equal(['Old', 'New']);
          expect(await names(member)).to.deep.equal(['Old']);
          // The owner unpinning clears it; a viewer's unpin leaves it.
          await projects.setPinned(old._id, member, false, false);
          expect((await projects.getLive(old._id))?.isPinned).to.equal(true);
          await projects.setPinned(old._id, owner, false, true);
          expect(await names(owner)).to.deep.equal(['New', 'Old']);
        });
      });

      it('a team the caller belongs to shares a project; a team of someone else does not', async () => {
        const org = newId(),
          owner = newId(),
          me = newId(),
          mine = newId(),
          theirs = newId();
        await inOrg(org, async () => {
          const p = await projects.create(org, owner, {
            name: 'team',
            tools: [],
          });
          await projects.upsertMembers(
            p._id,
            [{ principalId: mine, principalType: 'team', role: 'viewer' }],
            owner,
          );
          const q = {
            scope: 'shared' as const,
            includeArchived: false,
            skip: 0,
            limit: 5,
          };
          expect(
            (await projects.list(org, me, { ...q, teamIds: [mine] })).total,
          ).to.equal(1);
          expect(
            (await projects.list(org, me, { ...q, teamIds: [theirs] })).total,
          ).to.equal(0);
          expect(
            (await projects.list(org, me, { ...q, teamIds: [] })).total,
          ).to.equal(0);
        });
      });
    });

    describe('connectors config and Slack threads', () => {
      it('two creates of one connector leave one row and both answer with it', async () => {
        const org = newId(),
          u = newId();
        const both = await Promise.all(
          [1, 2].map(() =>
            inOrg(org, () => connectors.createEnabled(org, 'Drive', u)),
          ),
        );
        expect(both[0]?._id).to.equal(both[1]?._id);
        expect(await count(org, 'platform.connectors_config')).to.equal(1);
      });

      it('a thread is found and saved as the install organisation with no organisation in context, and in the organisation that is', async () => {
        const row = { threadId: 'T1', botId: 'B1', email: 'a@x.io' };
        await outsideAnyOrg(() =>
          slack.saveConversationId({ ...row, conversationId: 'c1' }),
        );
        expect(
          await stack.admin(
            async (c) =>
              (
                await c.query(
                  'SELECT org_id FROM integrations.slack_threads WHERE thread_id = $1',
                  ['T1'],
                )
              ).rows[0]?.org_id,
          ),
        ).to.equal(INSTALL_ORG);
        expect(
          await outsideAnyOrg(() =>
            slack.getConversationId('T1', 'B1', 'a@x.io'),
          ),
        ).to.equal('c1');
        expect(
          await outsideAnyOrg(() =>
            slack.getConversationId('T1', 'B1', 'other@x.io'),
          ),
        ).to.equal(null);
        // another organisation sees none of it
        expect(
          await inOrg(newId(), () =>
            slack.getConversationId('T1', 'B1', 'a@x.io'),
          ),
        ).to.equal(null);
        await inOrg(INSTALL_ORG, () =>
          slack.saveConversationId({ ...row, conversationId: 'c2' }),
        );
        expect(
          await outsideAnyOrg(() =>
            slack.getConversationId('T1', 'B1', 'a@x.io'),
          ),
        ).to.equal('c2');
      });
    });

    describe('mail log', () => {
      it('a row lands in the partition of its month, an old one in the default partition, and the org is the unit’s', async () => {
        const org = newId();
        await inOrg(org, () =>
          mail.record({
            orgId: org,
            subject: 's',
            from: 'f@x.io',
            to: ['a@x.io'],
            cc: ['c@x.io'],
            emailTemplateType: 'welcome',
          }),
        );
        const month = `y${new Date().getUTCFullYear()}m${String(new Date().getUTCMonth() + 1).padStart(2, '0')}`;
        const where = await asOrg<{ p: string }>(
          org,
          '/* pipeshub:test.partition */ SELECT tableoid::regclass::text AS p FROM platform.mail_log',
        );
        expect(where.map((r) => r.p)).to.deep.equal([
          `platform.mail_log_${month}`,
        ]);
        await stack.admin((c) =>
          c.query(
            `INSERT INTO platform.mail_log (org_id, id, created_at, from_address, to_addresses, email_template_type, updated_at)
               VALUES ($1, '0000000000000000000000f3', '2020-01-15T00:00:00Z', 'f', '{t}', 'old', now())`,
            [org],
          ),
        );
        const old = await asOrg<{ p: string }>(
          org,
          "/* pipeshub:test.partition */ SELECT tableoid::regclass::text AS p FROM platform.mail_log WHERE email_template_type = 'old'",
        );
        expect(old[0]?.p).to.equal('platform.mail_log_def');
      });

      it('an entry for another organisation, or a call with no organisation, is refused and writes nothing', async () => {
        const org = newId(),
          other = newId();
        let wrong: unknown, none: unknown;
        try {
          await inOrg(org, () =>
            mail.record({
              orgId: other,
              from: 'f@x.io',
              to: ['a@x.io'],
              emailTemplateType: 'x',
            }),
          );
        } catch (e) {
          wrong = e;
        }
        try {
          await outsideAnyOrg(() =>
            mail.record({
              orgId: org,
              from: 'f@x.io',
              to: ['a@x.io'],
              emailTemplateType: 'x',
            }),
          );
        } catch (e) {
          none = e;
        }
        expect(wrong).to.be.instanceOf(InternalServerError);
        expect(none).to.be.instanceOf(Error);
        expect(await count(org, 'platform.mail_log')).to.equal(0);
        expect(await count(other, 'platform.mail_log')).to.equal(0);
      });
    });
  });
}
