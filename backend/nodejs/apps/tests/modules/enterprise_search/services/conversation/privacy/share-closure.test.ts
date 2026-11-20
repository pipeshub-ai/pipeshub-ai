import { expect } from 'chai';
import { sealAnswerForStorage } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label';
import type {
  AnswerProvenance,
  LabelDirect,
  LabelParent,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label.types';
import {
  consentCoversViewer,
  fullCheck,
  ShareClosureDeps,
  ShareableRow,
  widenCheck,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/share-closure';

const ORG = 'org-1';
const ROOM = 'room-1';
const DANA = 'dana';
const CAROL = 'carol';
const BOB = 'bob';
const R = 'record-R';
const Q = 'record-Q';

let next = 0;
const mint = (): string => `ver-${(next += 1)}`;

const labelOf = (direct: Partial<LabelDirect> = {}, parents: LabelParent[] = []): AnswerProvenance => ({
  v: 5,
  ver: '',
  contentHash: '',
  direct: {
    records: [],
    vrids: [],
    nodes: [],
    saVrids: [],
    personal: [],
    agents: [],
    chat: [],
    projects: [],
    marks: [],
    ...direct,
  },
  parents,
  complete: true,
  tagset: 1,
  pub: false,
});

/** A stored answer row, sealed as the writers store it: its label bound to its version and text. */
const answer = (
  id: string,
  by: string,
  options: { direct?: Partial<LabelDirect>; parents?: LabelParent[]; session?: string; extra?: Partial<ShareableRow> } = {},
): ShareableRow & { labelVer: string } => {
  const sealed = sealAnswerForStorage(
    {
      messageType: 'bot_response',
      content: `the answer ${id}`,
      parts: [],
      provenance: labelOf(options.direct, options.parents),
    },
    mint,
  );
  return {
    _id: id,
    orgId: ORG,
    sessionId: options.session ?? ROOM,
    requestedBy: by,
    ...sealed,
    ...options.extra,
  } as ShareableRow & { labelVer: string };
};

const parentOf = (row: { _id: unknown; labelVer: string }, mode: LabelParent['mode'] = 'text'): LabelParent => ({
  id: String(row._id),
  ver: row.labelVer,
  mode,
});

interface World {
  rows: ShareableRow[];
  opens?: Record<string, string[]>;
  checkFails?: boolean;
  serviceAgents?: string[];
  sessionAgents?: Record<string, string>;
}

const depsOf = (world: World): ShareClosureDeps => ({
  readable: async (_org, userId, ids) =>
    world.checkFails ? null : new Set(ids.filter((id) => (world.opens?.[userId] ?? []).includes(id))),
  row: async (_org, id) => world.rows.find((r) => String(r._id) === id) ?? null,
  sessionAgent: async (_org, sessionId) => world.sessionAgents?.[sessionId],
  agentIsService: async (_org, _user, key) => (world.serviceAgents ?? []).includes(key),
  canUseAgent: async () => null,
});

const everyoneShare = (row: { labelVer: string }) => ({
  consent: { by: DANA, at: new Date(), ver: row.labelVer, scope: 'everyone' as const },
});

const peopleShare = (row: { labelVer: string }, by: string, to: Array<[string, string]>) => ({
  consent: { by, at: new Date(), ver: row.labelVer, scope: 'people' as const },
  consentTo: to.map(([userId, memberSince]) => ({ userId, memberSince })),
});

describe('the share check: an answer and everything it built on', () => {
  describe('widenCheck', () => {
    it('the asker can share an answer when she can open everything it used', async () => {
      const a = answer('a', DANA, { direct: { records: [R], nodes: ['kb-1'] } });
      const out = await widenCheck(depsOf({ rows: [a], opens: { [DANA]: [R, 'kb-1'] } }), a, a.labelVer, 'everyone');
      expect(out).to.deep.equal({ ok: true });
    });

    it('checks afresh: a record she can no longer open refuses the share', async () => {
      const a = answer('a', DANA, { direct: { records: [R, Q] } });
      const out = await widenCheck(depsOf({ rows: [a], opens: { [DANA]: [Q] } }), a, a.labelVer, 'everyone');
      expect(out).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' });
    });

    it('a check that cannot run refuses', async () => {
      const a = answer('a', DANA, { direct: { records: [R] } });
      const out = await widenCheck(depsOf({ rows: [a], checkFails: true }), a, a.labelVer, 'everyone');
      expect(out.ok).to.equal(false);
    });

    it('an answer whose label is incomplete, stale, or missing cannot be shared', async () => {
      const incomplete = answer('i', DANA);
      (incomplete.provenance as AnswerProvenance).complete = false;
      const stale = { ...answer('s', DANA), content: 'the text after the label was written' };
      const missing = { ...answer('m', DANA), provenance: undefined };
      for (const row of [incomplete, stale, missing]) {
        const out = await widenCheck(depsOf({ rows: [row] }), row, row.labelVer, 'everyone');
        expect(out, String(row._id)).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' });
      }
    });

    it('only the version she saw: an older version id is refused', async () => {
      const a = answer('a', DANA);
      const out = await widenCheck(depsOf({ rows: [a] }), a, 'an-older-version', 'everyone');
      expect(out.ok).to.equal(false);
    });

    it("her own tool's result can be shared by her, someone else's never", async () => {
      const own = answer('own', DANA, { direct: { personal: [DANA] } });
      const theirs = answer('theirs', DANA, { direct: { personal: [CAROL] } });
      expect((await widenCheck(depsOf({ rows: [own] }), own, own.labelVer, 'everyone')).ok).to.equal(true);
      expect((await widenCheck(depsOf({ rows: [theirs] }), theirs, theirs.labelVer, 'everyone')).ok).to.equal(false);
    });

    it('an upload from another chat refuses; one from this chat does not', async () => {
      const here = answer('here', DANA, { direct: { chat: [{ conversationId: ROOM, recordId: 'u1' }] } });
      const elsewhere = answer('else', DANA, { direct: { chat: [{ conversationId: 'another-chat', recordId: 'u1' }] } });
      expect((await widenCheck(depsOf({ rows: [here] }), here, here.labelVer, 'everyone')).ok).to.equal(true);
      expect((await widenCheck(depsOf({ rows: [elsewhere] }), elsewhere, elsewhere.labelVer, 'everyone')).ok).to.equal(false);
    });

    it('a service agent answer, a search as a service identity, or a chat of a service agent: refused as agent content', async () => {
      const service = answer('svc', DANA, { direct: { agents: [{ key: 'sales-bot', kind: 'service' }] } });
      const identity = answer('id', DANA, { direct: { saVrids: ['v1'] } });
      const guest = answer('guest', DANA, { extra: { respondingAgentKey: 'sales-bot' } });
      const inAgentChat = answer('in-chat', DANA, { session: 'agent-chat' });
      const world = { serviceAgents: ['sales-bot'], sessionAgents: { 'agent-chat': 'sales-bot' } };
      for (const row of [service, identity, guest, inAgentChat]) {
        const out = await widenCheck(depsOf({ rows: [row], ...world }), row, row.labelVer, 'everyone');
        expect(out, String(row._id)).to.deep.equal({ ok: false, code: 'SHARE_NOT_ALLOWED_AGENT' });
      }
    });

    it("a reply to a guest service agent's answer is refused like the agent's own answer, at any depth", async () => {
      const agentAnswer = answer('agent', CAROL, { extra: { respondingAgentKey: 'sales-bot' } });
      const reply = answer('reply', DANA, { parents: [parentOf(agentAnswer)] });
      const deeper = answer('deeper', DANA, { parents: [parentOf(reply)] });
      const world = { rows: [agentAnswer, reply, deeper], serviceAgents: ['sales-bot'] };
      expect(await widenCheck(depsOf(world), deeper, deeper.labelVer, 'everyone')).to.deep.equal({
        ok: false,
        code: 'SHARE_NOT_ALLOWED_AGENT',
      });
    });

    it('an agent whose status cannot be read is treated as a service agent', async () => {
      const row = answer('row', DANA, { extra: { respondingAgentKey: 'gone' } });
      const deps = { ...depsOf({ rows: [row] }), agentIsService: async () => null };
      expect(await widenCheck(deps, row, row.labelVer, 'everyone')).to.deep.equal({
        ok: false,
        code: 'SHARE_NOT_ALLOWED_AGENT',
      });
    });

    it("an agent's settings are never shown to everyone or outside the chat", async () => {
      const row = answer('cfg', DANA, { direct: { agents: [{ key: 'helper', kind: 'config' }] } });
      for (const audience of ['everyone', 'outside'] as const) {
        expect((await widenCheck(depsOf({ rows: [row] }), row, row.labelVer, audience)).code).to.equal('SHARE_NOT_ALLOWED_AGENT');
      }
    });

    it('a follow-up on an answer shared only with her cannot be shared, however many follow-ups back', async () => {
      // Carol's answer used R, which only Carol can open; she shared it with Dana alone.
      const c = answer('c', CAROL, { direct: { records: [R] } });
      Object.assign(c, peopleShare(c, CAROL, [[DANA, 'direct:1']]));
      const d1 = answer('d1', DANA, { parents: [parentOf(c)] });
      const d2 = answer('d2', DANA, { parents: [parentOf(d1)] });
      const d3 = answer('d3', DANA, { parents: [parentOf(d2)] });
      const world = { rows: [c, d1, d2, d3], opens: { [DANA]: [] as string[], [CAROL]: [R] } };
      for (const row of [d1, d2, d3]) {
        for (const audience of ['everyone', { userIds: [BOB] }, 'outside'] as const) {
          const out = await widenCheck(depsOf(world), row, row.labelVer, audience);
          expect(out, `${String(row._id)} ${JSON.stringify(audience)}`).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' });
        }
      }
    });

    it('a follow-up on an answer shared with everyone can be shared inside the chat, never outside it', async () => {
      const c = answer('c', CAROL, { direct: { records: [R] } });
      Object.assign(c, everyoneShare(c));
      const d = answer('d', DANA, { parents: [parentOf(c)] });
      const world = { rows: [c, d], opens: { [DANA]: [] as string[] } };
      expect((await widenCheck(depsOf(world), d, d.labelVer, 'everyone')).ok).to.equal(true);
      expect((await widenCheck(depsOf(world), d, d.labelVer, { userIds: [BOB] })).ok).to.equal(true);
      expect((await widenCheck(depsOf(world), d, d.labelVer, 'outside')).ok).to.equal(false);
    });

    it('a parent she can open herself passes by her own access', async () => {
      const c = answer('c', DANA, { direct: { records: [R] } });
      const d = answer('d', DANA, { parents: [parentOf(c, 'full')] });
      const world = { rows: [c, d], opens: { [DANA]: [R] } };
      expect((await widenCheck(depsOf(world), d, d.labelVer, 'outside')).ok).to.equal(true);
    });

    it('a grant, a pre-switch view or a file is never something to pass on', async () => {
      const c = answer('c', DANA);
      for (const parent of [
        parentOf(c, 'grant'),
        parentOf(c, 'preswitch'),
        { id: '', ver: '', mode: 'full' as const, artifactId: 'file-1' },
      ]) {
        const d = answer('d', DANA, { parents: [parent] });
        const out = await widenCheck(depsOf({ rows: [c, d] }), d, d.labelVer, 'everyone');
        expect(out.ok, JSON.stringify(parent)).to.equal(false);
      }
    });

    it('a parent at a version that is no longer kept refuses', async () => {
      const c = answer('c', DANA);
      const d = answer('d', DANA, { parents: [{ id: 'c', ver: 'a-version-nobody-kept', mode: 'text' }] });
      expect((await widenCheck(depsOf({ rows: [c, d] }), d, d.labelVer, 'everyone')).ok).to.equal(false);
    });

    it('a parent is judged at the version it was given, from the archive, with the share that version had', async () => {
      const c = answer('c', CAROL, { direct: { records: [R] } });
      const archived = { ver: c.labelVer, provenance: c.provenance, consent: everyoneShare(c).consent };
      // Carol regenerated it since: the live version is another one, never shared.
      const regenerated = { ...answer('c', CAROL, { direct: { records: [R] } }), labelHistory: [archived] };
      const d = answer('d', DANA, { parents: [parentOf(c)] });
      const world = { rows: [regenerated, d], opens: { [DANA]: [] as string[] } };
      expect((await widenCheck(depsOf(world), d, d.labelVer, 'everyone')).ok).to.equal(true);
    });
  });

  describe('consentCoversViewer', () => {
    it('a share with everyone covers every member now and later, and nobody outside the chat', () => {
      const a = answer('a', DANA);
      Object.assign(a, everyoneShare(a));
      expect(consentCoversViewer(a, a.labelVer, { userId: BOB, member: true, memberSince: 'direct:1' })).to.equal(true);
      // Priya was added later, through a team: no row of her own, still a member.
      expect(consentCoversViewer(a, a.labelVer, { userId: 'priya', member: true })).to.equal(true);
      expect(consentCoversViewer(a, a.labelVer, { userId: 'zed', member: false })).to.equal(false);
    });

    it('a share with a person covers that person in that membership only: a removal and re-add needs a new share', () => {
      const a = answer('a', DANA);
      Object.assign(a, peopleShare(a, DANA, [[BOB, 'direct:2026-11-01T00:00:00.000Z']]));
      expect(consentCoversViewer(a, a.labelVer, { userId: BOB, member: true, memberSince: 'direct:2026-11-01T00:00:00.000Z' })).to.equal(true);
      expect(consentCoversViewer(a, a.labelVer, { userId: BOB, member: true, memberSince: 'direct:2026-11-19T00:00:00.000Z' })).to.equal(false);
      expect(consentCoversViewer(a, a.labelVer, { userId: BOB, member: true })).to.equal(false);
      expect(consentCoversViewer(a, a.labelVer, { userId: BOB, member: false, memberSince: 'direct:2026-11-01T00:00:00.000Z' })).to.equal(false);
      expect(consentCoversViewer(a, a.labelVer, { userId: CAROL, member: true, memberSince: 'direct:2026-11-01T00:00:00.000Z' })).to.equal(false);
    });

    it('a share covers the version that was shared, not one written since', () => {
      const a = answer('a', DANA);
      Object.assign(a, everyoneShare(a));
      const regenerated = { ...a, ...answer('a', DANA), consent: a.consent };
      expect(consentCoversViewer(regenerated, regenerated.labelVer, { userId: BOB, member: true, memberSince: 'direct:1' })).to.equal(false);
    });
  });

  describe('fullCheck', () => {
    it('a viewer who can open everything sees the answer; one who can open part of it does not', async () => {
      const a = answer('a', DANA, { direct: { records: [R, Q] } });
      const world = { rows: [a], opens: { [CAROL]: [R, Q], ivy: [R] } };
      expect(await fullCheck(depsOf(world), { userId: CAROL }, a, a.labelVer)).to.equal(true);
      expect(await fullCheck(depsOf(world), { userId: 'ivy' }, a, a.labelVer)).to.equal(false);
    });

    it('a parent shared with the viewer stands in for what they cannot open; one shared with someone else does not', async () => {
      const c = answer('c', CAROL, { direct: { records: [R] } });
      Object.assign(c, peopleShare(c, CAROL, [[BOB, 'direct:1']]));
      const d = answer('d', BOB, { parents: [parentOf(c)] });
      const world = { rows: [c, d], opens: {} };
      expect(await fullCheck(depsOf(world), { userId: BOB, member: true, memberSince: 'direct:1' }, d, d.labelVer)).to.equal(true);
      expect(await fullCheck(depsOf(world), { userId: 'ivy', member: true, memberSince: 'direct:1' }, d, d.labelVer)).to.equal(false);
    });
  });
});
