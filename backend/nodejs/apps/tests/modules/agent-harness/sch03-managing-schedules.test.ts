/** ADR-SCH-03: a schedule is changed, skipped, found and deleted from chat, with every check a create makes. */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import sinon from 'sinon';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import {
  ChatScheduleService,
  SCHEDULE_DELETE_TOOL_ID,
  SCHEDULE_UPDATE_TOOL_ID,
  updateArgumentsProblem,
} from '../../../src/modules/agent-harness/application/chat-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  type ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { scheduleDeleteArgumentsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/schedule-delete-arguments';
import { scheduleFindArgumentsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/schedule-find-arguments';
import { scheduleUpdateArgumentsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/schedule-update-arguments';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { type HarnessRun, resolvedData } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import {
  nextCronSlot,
  RAN_ONCE,
  SKIPPED_BY_OWNER,
} from '../../../src/modules/agent-harness/domain/schedules';
import { AgentHarnessChatScheduleController } from '../../../src/modules/agent-harness/transport/chat-schedule-controller';
import { testControlPlane } from '../control-plane/support/in-memory';
import {
  ADMIN_USER,
  adminReach,
  ALICE,
  BOB,
  FakeActors,
  FakeAdmission,
  FakeSetting,
  ORG,
  TestClock,
} from './support/schedules';

const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
const admin: ScheduleCaller = { orgId: ORG, userId: ADMIN_USER, via: 'session' };
const CRON = {
  name: 'Standup summary',
  input: { query: 'Summarise yesterday' },
  trigger: { kind: 'cron' as const, cron: '0 9 * * 1-5', timeZone: 'Europe/Berlin' },
};
const MIN = 60_000;

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof HarnessScheduleRefusedError) return error.code;
    throw error;
  }
  throw new Error('expected a refusal');
}

function world() {
  const clock = new TestClock();
  const store = new InMemoryScheduleStore(clock);
  const actors = new FakeActors();
  const setting = new FakeSetting();
  const admission = new FakeAdmission();
  const firer = new ScheduleFirer({ store, admission, actors, setting });
  const chats = new Map<string, { userId: string; agentKey: string | null }>([
    ['chat-a', { userId: ALICE, agentKey: null }],
    ['chat-agent', { userId: ALICE, agentKey: 'helper' }],
    ['chat-bob', { userId: BOB, agentKey: null }],
  ]);
  const service = new ScheduleService({
    transaction: testControlPlane(),
    store,
    firer,
    actors,
    setting,
    reach: adminReach(actors),
    conversations: {
      ownedBy: async (_orgId, userId, id) => {
        const chat = chats.get(id);
        return chat?.userId === userId ? { agentKey: chat.agentKey } : null;
      },
    },
  });
  const once = (inMs: number, name = 'Joke in two minutes') =>
    service.create(alice, {
      name,
      input: { query: 'Tell me a short joke' },
      trigger: { kind: 'once', at: new Date(clock.value + inMs).toISOString(), timeZone: 'UTC' },
    });
  return { clock, store, actors, setting, admission, firer, service, once };
}

describe('ADR-SCH-03 managing schedules from chat', () => {
  describe('D4: an edit is checked as a create checks it', () => {
    it('changes the ask, the name and the notices; bumps the revision; records the chat', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      expect(made.revision).to.equal(0);
      const from = await w.service.changedFrom(alice, 'chat-agent');
      const changed = await w.service.update(
        alice,
        made.id,
        { revision: 0, name: 'Morning summary', query: 'Summarise yesterday and open bugs', notify: ['in_app', 'email'] },
        from,
      );
      expect(changed).to.deep.include({ name: 'Morning summary', revision: 1, notify: ['in_app', 'email'] });
      expect(changed.input?.query).to.equal('Summarise yesterday and open bugs');
      expect(changed.lastChangedFrom).to.deep.include({ conversationId: 'chat-agent', agentKey: 'helper' });
      expect(changed.nextFireAt).to.equal(made.nextFireAt);
    });

    it('a stale revision is refused SCHEDULE_CHANGED and nothing changes', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      await w.service.pause(alice, made.id);
      expect(await code(w.service.update(alice, made.id, { revision: 0, name: 'x' }))).to.equal('SCHEDULE_CHANGED');
      expect((await w.service.get(alice, made.id)).name).to.equal(CRON.name);
      expect(await code(w.service.assertRevision(alice, made.id, 0))).to.equal('SCHEDULE_CHANGED');
      expect((await w.service.assertRevision(alice, made.id, 1)).id).to.equal(made.id);
    });

    it('two edits from one read: one lands, the other is refused by the store', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      const both = await Promise.allSettled([
        w.service.update(alice, made.id, { revision: 0, name: 'One' }),
        w.service.update(alice, made.id, { revision: 0, name: 'Two' }),
      ]);
      expect(both.filter((r) => r.status === 'fulfilled')).to.have.length(1);
      const lost = both.find((r) => r.status === 'rejected') as PromiseRejectedResult;
      expect((lost.reason as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_CHANGED');
    });

    it('a recurring schedule moved takes effect from its next slot', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      const moved = await w.service.update(alice, made.id, {
        revision: 0,
        trigger: { kind: 'cron', cron: '30 8 * * 1-5', timeZone: 'Europe/Berlin' },
      });
      expect(Date.parse(moved.nextFireAt!)).to.equal(nextCronSlot('30 8 * * 1-5', 'Europe/Berlin', w.clock.value));
      // Still the cron floor and every other check of a create.
      expect(
        await code(w.service.update(alice, made.id, { revision: 1, trigger: { kind: 'cron', cron: '* * * * *', timeZone: 'UTC' } })),
      ).to.equal('SCHEDULE_INVALID');
    });

    it('a one-off moved keeps its one slot; its window is checked', async () => {
      const w = world();
      const made = (await w.once(5 * MIN)).schedule;
      const at = new Date(w.clock.value + 10 * MIN).toISOString();
      const moved = await w.service.update(alice, made.id, { revision: 0, trigger: { kind: 'once', at, timeZone: 'UTC' } });
      expect(moved.trigger).to.deep.include({ kind: 'once', at });
      expect(moved.nextFireAt).to.equal(at);
      expect(
        await code(
          w.service.update(alice, made.id, {
            revision: 1,
            trigger: { kind: 'once', at: new Date(w.clock.value + 10_000).toISOString(), timeZone: 'UTC' },
          }),
        ),
      ).to.equal('SCHEDULE_INVALID');
      w.clock.value = Date.parse(at) + 1_000;
      await w.firer.fireDue();
      expect(await w.store.firings(ORG, made.id, 10)).to.have.length(1);
    });

    it('a one-off that ran cannot be edited (SCHEDULE_RAN_ONCE)', async () => {
      const w = world();
      const made = (await w.once(5 * MIN)).schedule;
      w.clock.value += 6 * MIN;
      await w.firer.fireDue();
      const after = await w.store.get(ORG, made.id);
      expect(after?.statusReason).to.equal(RAN_ONCE);
      expect(await code(w.service.update(alice, made.id, { revision: after!.revision ?? 0, query: 'An animal joke' }))).to.equal(
        'SCHEDULE_RAN_ONCE',
      );
    });

    it('only the owner edits; an admin who may pause it may not change it; a service account never', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      expect(await code(w.service.update(admin, made.id, { revision: 0, name: 'x' }))).to.equal('SCHEDULE_FORBIDDEN');
      expect(await code(w.service.update({ ...alice, via: 'service' }, made.id, { revision: 0, name: 'x' }))).to.equal(
        'SCHEDULE_FORBIDDEN',
      );
      expect(await code(w.service.update({ ...alice, userId: BOB }, made.id, { revision: 0, name: 'x' }))).to.equal(
        'SCHEDULE_NOT_FOUND',
      );
      expect((await w.service.pause(admin, made.id)).status).to.equal('paused');
    });

    it('a paused schedule moved keeps no cursor; resume works it out; nothing to change is refused', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      await w.service.pause(alice, made.id);
      const moved = await w.service.update(alice, made.id, {
        revision: 1,
        trigger: { kind: 'cron', cron: '0 10 * * *', timeZone: 'UTC' },
      });
      expect([moved.status, moved.nextFireAt]).to.deep.equal(['paused', null]);
      const resumed = await w.service.resume(alice, made.id);
      expect(Date.parse(resumed.nextFireAt!)).to.equal(nextCronSlot('0 10 * * *', 'UTC', w.clock.value));
      expect(resumed.revision).to.equal(3);
      expect(await code(w.service.update(alice, made.id, { revision: 3 }))).to.equal('SCHEDULE_INVALID');
    });

    it('an event schedule cannot be given a time', async () => {
      const w = world();
      const made = (await w.service.create(alice, { name: 'Hook', input: { query: 'x' }, trigger: { kind: 'webhook' } })).schedule;
      expect(
        await code(w.service.update(alice, made.id, { revision: 0, trigger: { kind: 'cron', cron: '0 9 * * *', timeZone: 'UTC' } })),
      ).to.equal('SCHEDULE_INVALID');
      expect((await w.service.update(alice, made.id, { revision: 0, name: 'Renamed hook' })).name).to.equal('Renamed hook');
    });

    it('a delete or a pause shown an older revision is refused SCHEDULE_CHANGED and changes nothing', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      await w.service.update(alice, made.id, { revision: 0, name: 'Moved on' });
      expect(await code(w.service.delete(alice, made.id, 0))).to.equal('SCHEDULE_CHANGED');
      expect(await code(w.service.pause(alice, made.id, undefined, 0))).to.equal('SCHEDULE_CHANGED');
      expect((await w.service.get(alice, made.id)).status).to.equal('active');
      expect((await w.service.pause(alice, made.id, undefined, 1)).status).to.equal('paused');
      await w.service.delete(alice, made.id, 2);
      expect(await w.store.get(ORG, made.id)).to.equal(null);
    });

    it('a change names only one of the caller’s own chats', async () => {
      const w = world();
      expect(await code(w.service.changedFrom(alice, 'chat-bob'))).to.equal('SCHEDULE_FORBIDDEN');
      expect(await code(w.service.changedFrom(alice, 'no-such-chat'))).to.equal('SCHEDULE_FORBIDDEN');
    });
  });

  describe('D4: skip next', () => {
    it('the next slot is recorded skipped, the one after runs; asking twice changes nothing', async () => {
      const w = world();
      const made = (await w.service.create(alice, CRON)).schedule;
      const skipped = await w.service.skipNext(alice, made.id, 0);
      expect(skipped.skipNextAt).to.equal(made.nextFireAt);
      expect((await w.service.skipNext(alice, made.id, undefined)).revision).to.equal(1);
      w.clock.value = Date.parse(made.nextFireAt!) + 1_000;
      await w.firer.fireDue();
      const [first] = await w.store.firings(ORG, made.id, 10);
      expect([first!.status, first!.reason]).to.deep.equal(['skipped', SKIPPED_BY_OWNER]);
      expect(w.admission.admitted).to.equal(0);
      const next = await w.store.get(ORG, made.id);
      expect((await w.service.get(alice, made.id)).skipNextAt).to.equal(null);
      w.clock.value = next!.nextFireAt! + 1_000;
      await w.firer.fireDue();
      expect(w.admission.admitted).to.equal(1);
    });

    it('a one-off, a paused or an event schedule has no next run to skip', async () => {
      const w = world();
      const one = (await w.once(5 * MIN)).schedule;
      expect(await code(w.service.skipNext(alice, one.id, 0))).to.equal('SCHEDULE_INVALID');
      const cron = (await w.service.create(alice, CRON)).schedule;
      await w.service.pause(alice, cron.id);
      expect(await code(w.service.skipNext(alice, cron.id, 1))).to.equal('SCHEDULE_INVALID');
      expect(await code(w.service.skipNext(alice, cron.id, 0))).to.equal('SCHEDULE_CHANGED');
      expect(await code(w.service.skipNext(admin, cron.id, 1))).to.equal('SCHEDULE_FORBIDDEN');
    });
  });

  describe('D2: finding is a read of the person’s own schedules', () => {
    it('by a part of the name and by state; this chat’s first, then the next to run', async () => {
      const w = world();
      const late = (await w.service.create(alice, { ...CRON, name: 'Evening digest', trigger: { ...CRON.trigger, cron: '0 20 * * *' } })).schedule;
      const early = (await w.service.create(alice, { ...CRON, name: 'Morning standup', trigger: { ...CRON.trigger, cron: '0 7 * * *' } })).schedule;
      const here = (
        await w.service.create(alice, { ...CRON, name: 'Standup here', trigger: { ...CRON.trigger, cron: '0 22 * * *' } }, { origin: { conversationId: 'chat-a' } })
      ).schedule;
      await w.service.create({ ...alice, userId: BOB }, { ...CRON, name: 'Bob standup' });
      await w.service.pause(alice, late.id);
      const all = await w.service.list(alice, 20, false, { conversationId: 'chat-a' });
      expect(all.map((s) => s.id)).to.deep.equal([here.id, early.id, late.id]);
      expect(all.map((s) => s.madeHere)).to.deep.equal([true, false, false]);
      expect((await w.service.list(alice, 20, false, { query: 'STANDUP' })).map((s) => s.name).sort()).to.deep.equal([
        'Morning standup',
        'Standup here',
      ]);
      expect((await w.service.list(alice, 20, false, { status: 'paused' })).map((s) => s.id)).to.deep.equal([late.id]);
      expect(
        (await w.service.list(alice, 20, false, { conversationId: 'chat-a', madeHere: true })).map((s) => s.id),
      ).to.deep.equal([here.id]);
      expect(await w.service.list(alice, 1, false, { conversationId: 'chat-a' })).to.have.length(1);
    });
  });

  describe('contracts: the shared vectors, the one rule in code', () => {
    it('every vector gives its verdict', () => {
      const { vectors } = JSON.parse(
        readFileSync(resolve(__dirname, '../../../../../../contracts/harness/vectors/schedule-manage-arguments.json'), 'utf8'),
      ) as { vectors: Array<{ tool: 'update' | 'delete' | 'find'; name: string; arguments: unknown; valid: boolean }> };
      expect(vectors.length).to.be.greaterThan(20);
      for (const v of vectors) {
        const schema =
          v.tool === 'update'
            ? scheduleUpdateArgumentsSchema
            : v.tool === 'delete'
              ? scheduleDeleteArgumentsSchema
              : scheduleFindArgumentsSchema;
        const parsed = schema.safeParse(v.arguments);
        const valid =
          parsed.success &&
          (v.tool !== 'update' || updateArgumentsProblem(parsed.data as never) === null);
        expect(valid, v.name).to.equal(v.valid);
      }
    });
  });

  describe('D6: a harness change acts only on the person’s approval of that call', () => {
    const RUN = 'run-chat-3';
    const ixOf = (args: unknown) => `ix.approve.${canonicalDigest(args).slice(7, 23)}`;
    const requested = (interactionId: string, toolId: string, args: unknown): JournalEvent =>
      ({
        type: 'interaction.requested',
        runId: RUN,
        data: {
          interactionId,
          kind: 'approval',
          bindingDigest: canonicalDigest({ interactionId, kind: 'approval', subject: toolId, argsDigest: canonicalDigest(args) }),
        },
      }) as unknown as JournalEvent;
    const resolved = (interactionId: string, decision: 'approved' | 'denied'): JournalEvent =>
      ({
        type: 'interaction.resolved',
        runId: RUN,
        data: resolvedData({ interactionId, decision, channel: 'web' }, `p_${ALICE}`),
      }) as unknown as JournalEvent;

    function chatWorld() {
      const w = world();
      const events: JournalEvent[] = [];
      const run = {
        orgId: ORG,
        runId: RUN,
        sessionId: 'ses-chat',
        actorId: ALICE,
        agentKey: 'helper',
        input: { query: 'make it an animal joke', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
      } as unknown as HarnessRun;
      const chat = new ChatScheduleService({
        runs: { get: async (o, r) => (o === ORG && r === RUN ? run : null), runEvents: async () => events },
        vias: { via: async () => 'session' },
        schedules: w.service,
        store: w.store,
        actors: w.actors,
        marks: { record: async () => undefined },
      });
      const approve = (toolId: string, args: unknown, decision: 'approved' | 'denied' = 'approved') => {
        const ix = ixOf(args);
        events.push(requested(ix, toolId, args), resolved(ix, decision));
        return ix;
      };
      return { ...w, chat, events, approve };
    }

    it('an approved update changes it once, as the person, recording this chat and the call', async () => {
      const w = chatWorld();
      const made = (await w.service.create(alice, CRON)).schedule;
      const args = { id: made.id, revision: 0, ask: 'Summarise yesterday and the open bugs' };
      const ix = w.approve(SCHEDULE_UPDATE_TOOL_ID, args);
      const first = await w.chat.update(ORG, RUN, made.id, { interactionId: ix, toolId: SCHEDULE_UPDATE_TOOL_ID, arguments: args });
      expect(first.changed).to.equal(true);
      expect(first.schedule?.input?.query).to.equal(args.ask);
      expect(first.schedule?.lastChangedFrom).to.deep.include({ conversationId: 'ses-chat', agentKey: 'helper' });
      const again = await w.chat.update(ORG, RUN, made.id, { interactionId: ix, toolId: SCHEDULE_UPDATE_TOOL_ID, arguments: args });
      expect([again.changed, again.schedule?.revision]).to.deep.equal([false, 1]);
    });

    it('refused without the person’s own approval, for another schedule, other arguments or a mixed call', async () => {
      const w = chatWorld();
      const made = (await w.service.create(alice, CRON)).schedule;
      const args = { id: made.id, revision: 0, name: 'Renamed' };
      const call = (ix: string, a: Record<string, unknown> = args, toolId = SCHEDULE_UPDATE_TOOL_ID, id = made.id) =>
        w.chat.update(ORG, RUN, id, { interactionId: ix, toolId, arguments: a as never });
      expect(await code(call('ix.none'))).to.equal('SCHEDULE_NOT_APPROVED');
      const denied = w.approve(SCHEDULE_UPDATE_TOOL_ID, args, 'denied');
      expect(await code(call(denied))).to.equal('SCHEDULE_NOT_APPROVED');
      const ok = w.approve(SCHEDULE_UPDATE_TOOL_ID, { ...args, name: 'Asked' });
      expect(await code(call(ok, { ...args, name: 'Not what was asked' }))).to.equal('SCHEDULE_NOT_APPROVED');
      expect(await code(call(ok, { ...args, name: 'Asked' }, SCHEDULE_DELETE_TOOL_ID))).to.equal('SCHEDULE_NOT_APPROVED');
      expect(await code(call(ok, { ...args, name: 'Asked' }, SCHEDULE_UPDATE_TOOL_ID, 'another-id'))).to.equal(
        'SCHEDULE_NOT_APPROVED',
      );
      expect(await code(call(ok, { ...args, action: 'pause' }))).to.equal('SCHEDULE_INVALID');
    });

    it('an approved action pauses, skips or runs; a stale revision is SCHEDULE_CHANGED', async () => {
      const w = chatWorld();
      const made = (await w.service.create(alice, CRON)).schedule;
      const pause = { id: made.id, revision: 0, action: 'pause' as const };
      const paused = await w.chat.update(ORG, RUN, made.id, {
        interactionId: w.approve(SCHEDULE_UPDATE_TOOL_ID, pause),
        toolId: SCHEDULE_UPDATE_TOOL_ID,
        arguments: pause,
      });
      expect(paused.schedule?.status).to.equal('paused');
      const stale = { id: made.id, revision: 0, action: 'resume' as const };
      expect(
        await code(
          w.chat.update(ORG, RUN, made.id, {
            interactionId: w.approve(SCHEDULE_UPDATE_TOOL_ID, stale),
            toolId: SCHEDULE_UPDATE_TOOL_ID,
            arguments: stale,
          }),
        ),
      ).to.equal('SCHEDULE_CHANGED');
      const run = { id: made.id, revision: 1, action: 'run_now' as const };
      await w.chat.update(ORG, RUN, made.id, {
        interactionId: w.approve(SCHEDULE_UPDATE_TOOL_ID, run),
        toolId: SCHEDULE_UPDATE_TOOL_ID,
        arguments: run,
      });
      expect(w.admission.admitted).to.equal(1);
    });

    it('an approved delete removes it once; a repeat answers it is gone', async () => {
      const w = chatWorld();
      const made = (await w.service.create(alice, CRON)).schedule;
      const args = { id: made.id, revision: 0 };
      const ix = w.approve(SCHEDULE_DELETE_TOOL_ID, args);
      const call = () => w.chat.delete(ORG, RUN, made.id, { interactionId: ix, toolId: SCHEDULE_DELETE_TOOL_ID, arguments: args });
      expect(await call()).to.deep.equal({ schedule: null, changed: true });
      expect(await w.store.get(ORG, made.id)).to.equal(null);
      expect(await call()).to.deep.equal({ schedule: null, changed: false });
    });

    it('find reads the run’s person’s own schedules, this chat first; read refuses another person’s', async () => {
      const w = chatWorld();
      const mine = (await w.service.create(alice, CRON, { origin: { conversationId: 'ses-chat' } })).schedule;
      const bobs = (await w.service.create({ ...alice, userId: BOB }, CRON)).schedule;
      const found = await w.chat.find(ORG, RUN, {});
      expect(found.map((s) => [s.id, s.madeHere])).to.deep.equal([[mine.id, true]]);
      expect((await w.chat.read(ORG, RUN, mine.id)).id).to.equal(mine.id);
      expect(await code(w.chat.read(ORG, RUN, bobs.id))).to.equal('SCHEDULE_NOT_FOUND');
    });

    it('the controller parses the contract and answers 400 for a malformed call', async () => {
      const w = chatWorld();
      const controller = new AgentHarnessChatScheduleController(w.chat, { authorizeAttempt: sinon.stub().resolves() } as never);
      const next = sinon.spy();
      await controller.update(
        {
          params: { runId: RUN, scheduleId: 'sch_1' },
          body: { attemptId: 'a', fence: 1, interactionId: 'ix', toolId: SCHEDULE_UPDATE_TOOL_ID, arguments: { id: 'sch_1' } },
          harnessAttempt: { orgId: ORG, runId: RUN, attemptId: 'a', fence: 1 },
        } as never,
        { status: sinon.stub().returnsThis(), json: sinon.stub() } as never,
        next,
      );
      expect((next.firstCall.args[0] as { statusCode: number }).statusCode).to.equal(400);
    });
  });
});
