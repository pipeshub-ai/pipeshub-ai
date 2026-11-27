/**
 * UX5 AP-2..AP-8: the activity panel's routes over real HTTP: the session
 * routers with their scope checks and error middleware, the real
 * SessionRunService deciding who may watch, the real RunActivityService over
 * an in-memory journal, journal tail and activity bus, and the real content
 * service opening a sealed log. Every wait is on an event, never on time.
 */
import 'reflect-metadata';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import { Container } from 'inversify';
import sinon from 'sinon';
import { Logger } from '../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  AGENT_HARNESS_RUN_ACTIVITY,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunActivityService } from '../../../src/modules/agent-harness/application/run-activity-service';
import { RunContentService, sealRunContent } from '../../../src/modules/agent-harness/application/run-content-service';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryJournalTail } from '../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ChatSessionRepository } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import {
  ACTIVITY_LIVE_CHANNEL,
  ActivityFrame,
  ActivityView,
} from '../../../src/modules/agent-harness/projection/activity';
import { testCipher } from './support/data-protection';
import { ActivityJournal, activityScenario } from './support/activity-journal';
import { pinWith } from './support/execution-store-contract/run-policy';

const ORG = 'org-ux5';
const SESSION = 'ses-ux5';
const RUN_ID = 'run-ux5';

const identities: Record<string, Record<string, unknown>> = {
  owner: { orgId: ORG, userId: 'user-owner' },
  participant: { orgId: ORG, userId: 'user-participant' },
  viewer: { orgId: ORG, userId: 'user-viewer' },
  stranger: { orgId: ORG, userId: 'user-stranger' },
};

interface SseEvent {
  event: string;
  data: unknown;
}

/** Reads a streaming SSE response event by event; heartbeats are skipped. */
class SseReader {
  private buffer = '';
  private readonly reader: ReadableStreamDefaultReader<Uint8Array>;
  private readonly decoder = new TextDecoder();
  ended = false;

  constructor(response: Response) {
    this.reader = response.body!.getReader();
  }

  async next(): Promise<SseEvent | null> {
    for (;;) {
      const at = this.buffer.indexOf('\n\n');
      if (at >= 0) {
        const block = this.buffer.slice(0, at);
        this.buffer = this.buffer.slice(at + 2);
        if (block.startsWith(':')) continue;
        const lines = block.split('\n');
        const event = lines.find((l) => l.startsWith('event: '))!.slice('event: '.length);
        const data = JSON.parse(lines.find((l) => l.startsWith('data: '))!.slice('data: '.length)) as unknown;
        return { event, data };
      }
      const chunk = await this.reader.read();
      if (chunk.done) {
        this.ended = true;
        return null;
      }
      this.buffer += this.decoder.decode(chunk.value, { stream: true });
    }
  }

  async rest(): Promise<SseEvent[]> {
    const events: SseEvent[] = [];
    for (let event = await this.next(); event !== null; event = await this.next()) events.push(event);
    return events;
  }

  cancel(): Promise<void> {
    return this.reader.cancel();
  }
}

const frame = (n: number, kind: ActivityFrame['kind'], data: Record<string, unknown>): ActivityFrame => ({
  runId: RUN_ID,
  fence: 1,
  modelAttempt: 0,
  n,
  kind,
  data,
});

interface World {
  journal: ActivityJournal;
  run: HarnessRun;
  tail: InMemoryJournalTail;
  bus: InMemoryLiveBus<ActivityFrame>;
  panel: { on: boolean };
  blobs: InMemoryPayloadStore;
  cipher: ReturnType<typeof testCipher>;
  get: (path: string, bearer: string) => Promise<Response>;
  commit: (add: (journal: ActivityJournal) => void) => void;
  close: () => Promise<void>;
}

async function start(options: { bus?: boolean; journal?: ActivityJournal; status?: HarnessRun['status'] } = {}): Promise<World> {
  const journal = options.journal ?? activityScenario(new ActivityJournal(RUN_ID, SESSION, ORG));
  const run = {
    runId: RUN_ID,
    orgId: ORG,
    sessionId: SESSION,
    actorId: 'user-owner',
    agentKey: 'agent-1',
    harnessMajor: 2,
    status: options.status ?? 'running',
    createdAt: 1,
    updatedAt: 2,
  } as unknown as HarnessRun;
  const sessions = {
    findStreamAccess: (scope: { actorId: string; sessionId: string }) =>
      Promise.resolve(
        scope.sessionId !== SESSION
          ? null
          : scope.actorId === 'user-owner' || scope.actorId === 'user-participant'
            ? { audience: 'owner' }
            : scope.actorId === 'user-viewer'
              ? { audience: 'shared' }
              : null,
      ),
  } as unknown as ChatSessionRepository;
  const executions = {
    get: (_org: string, runId: string) => Promise.resolve(runId === run.runId ? run : null),
  } as unknown as ExecutionService;
  const tail = new InMemoryJournalTail();
  const bus = new InMemoryLiveBus<ActivityFrame>(4_000, ACTIVITY_LIVE_CHANNEL);
  const panel = { on: true };
  const blobs = new InMemoryPayloadStore();
  const cipher = testCipher();
  const runs = new SessionRunService(executions, sessions);
  const activity = new RunActivityService({
    runs,
    executions,
    journal: {
      readRun: (_org, runId, after, limit) =>
        Promise.resolve(journal.events.filter((e) => e.runId === runId && e.seq > after).slice(0, limit)),
    },
    journals: new JournalService(new InMemoryPayloadStore(), cipher),
    tail,
    bus: options.bus === false ? null : bus,
    setting: { enabled: () => Promise.resolve(panel.on) },
    content: new RunContentService(executions, cipher, blobs),
  });
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue({
    authenticate: (req: express.Request & { user?: unknown }, res: express.Response, next: express.NextFunction) => {
      const identity = identities[(req.headers.authorization ?? '').replace(/^Bearer /, '')];
      if (identity === undefined) {
        res.status(401).json({});
        return;
      }
      req.user = identity;
      next();
    },
  });
  container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue(runs);
  container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_RUN_ACTIVITY).toConstantValue(activity);
  const quiet = [sinon.stub(Logger.getInstance(), 'error'), sinon.stub(Logger.getInstance(), 'warn'), sinon.stub(Logger.getInstance(), 'info')];
  const app = express();
  app.use('/agents', createAgentHarnessSessionRouter(container));
  app.use('/conversations', createChatHarnessSessionRouter(container));
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return {
    journal,
    run,
    tail,
    bus,
    panel,
    blobs,
    cipher,
    get: (path, bearer) => fetch(`${base}${path}`, { headers: { authorization: `Bearer ${bearer}` } }),
    commit: (add) => {
      add(journal);
      tail.changed(ORG, RUN_ID);
    },
    close: () =>
      new Promise<void>((resolve) => {
        for (const stub of quiet) stub.restore();
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}

const RUN_PATH = `/agents/agent-1/conversations/${SESSION}/runs/${RUN_ID}`;
const ACTIVITY = `${RUN_PATH}/activity`;
const log = (commandId: string) => `${RUN_PATH}/commands/${commandId}/log`;
const step = (toolCallId: string) => `${RUN_PATH}/steps/${toolCallId}`;
const snapshotOf = (event: SseEvent | null): ActivityView => {
  expect(event?.event).to.equal('snapshot');
  return event!.data as ActivityView;
};

describe('agent-harness activity panel routes over HTTP (UX5)', () => {
  let world: World;
  afterEach(async () => {
    await world.close();
  });

  it('AP-2: a snapshot first, then the bus\'s command and output events, a new snapshot on each commit, and end after the run\'s end', async () => {
    world = await start();
    const reply = await world.get(ACTIVITY, 'owner');
    expect(reply.status).to.equal(200);
    expect(reply.headers.get('content-type')).to.equal('text/event-stream');
    const sse = new SseReader(reply);
    const first = snapshotOf(await sse.next());
    expect(first.status).to.equal('running');
    expect(first.commands.map((c) => [c.commandId, c.status])).to.deep.equal([
      ['t1.0', 'completed'],
      ['e1', 'failed'],
      ['e2', 'running'],
    ]);

    world.bus.publish(frame(1, 'command_started', { commandId: 'e3', source: 'engine', command: 'make', startedAt: 9, attemptId: 'a1' }));
    const started = await sse.next();
    expect(started).to.deep.equal({
      event: 'command',
      data: {
        commandId: 'e3', source: 'engine', command: 'make', status: 'running', exitCode: null, startedAt: 9,
        durationMs: null, stdout: '', stderr: '', truncated: false, liveDropped: 0, hasLog: false,
      },
    });
    world.bus.publish(frame(2, 'command_output', { commandId: 'e3', stream: 'stderr', text: 'cc: warning' }));
    expect(await sse.next()).to.deep.equal({ event: 'output', data: { commandId: 'e3', stream: 'stderr', text: 'cc: warning' } });
    world.bus.publish(frame(3, 'command_output', { commandId: 'e3', stream: 'stdout', dropped: 512 }));
    expect(await sse.next()).to.deep.equal({ event: 'output', data: { commandId: 'e3', stream: 'stdout', dropped: 512 } });
    world.bus.publish(frame(4, 'command_finished', { commandId: 'e3', status: 'completed', exitCode: 0, durationMs: 5 }));
    expect(await sse.next()).to.deep.include({ event: 'command' }).and.satisfy((e: SseEvent) => (e.data as { status: string }).status === 'completed');

    world.commit((j) => j.task('task.updated', 'task-2', 'Fix the parser', 'in_progress'));
    const advanced = snapshotOf(await sse.next());
    expect(advanced.tasks.find((t) => t.taskId === 'task-2')?.status).to.equal('in_progress');
    expect(advanced.commands.find((c) => c.commandId === 'e3'), 'the live command stays in the snapshot').to.deep.include({ status: 'completed', stderr: 'cc: warning', liveDropped: 512 });

    world.run.status = 'completed';
    world.run.followups = [{ text: 'Run the tests again?' }];
    world.commit((j) => j.completed());
    const last = snapshotOf(await sse.next());
    expect(last.status).to.equal('completed');
    expect(last.followups, 'from the run document').to.deep.equal([{ text: 'Run the tests again?' }]);
    expect(last.commands.find((c) => c.commandId === 'e2')?.status, 'cut off with the run').to.equal('cancelled');
    expect(await sse.next()).to.deep.equal({ event: 'end', data: { status: 'completed' } });
    expect(await sse.next()).to.equal(null);
    expect(world.tail.watching, 'nothing is followed once it ended').to.equal(0);
  });

  it("C410-U7: ui.terminal off in the run's pin: commands by name only, no live output, and no log", async () => {
    const journal = new ActivityJournal(RUN_ID, SESSION, ORG);
    journal.add('run.spec_pinned', {
      specDigest: `sha256:${'c'.repeat(64)}`,
      spec: { runKind: 'interactive', extensions: { 'x.pipeshub': { resolved: pinWith({ ui: { terminal: false } }) } } },
    });
    world = await start({ journal: activityScenario(journal) });
    const sse = new SseReader(await world.get(ACTIVITY, 'owner'));
    const first = snapshotOf(await sse.next());
    expect(first.outputHidden).to.equal(true);
    expect(first.commands.find((c) => c.commandId === 't1.0')).to.deep.include({ command: "print('hi')", stdout: '', hasLog: false });
    world.bus.publish(frame(1, 'command_started', { commandId: 'e3', source: 'engine', command: 'make', startedAt: 9 }));
    expect(await sse.next()).to.deep.include({ event: 'command' });
    world.bus.publish(frame(2, 'command_output', { commandId: 'e3', stream: 'stdout', text: 'secret' }));
    world.bus.publish(frame(3, 'command_finished', { commandId: 'e3', status: 'completed', exitCode: 0, durationMs: 5 }));
    const finished = await sse.next();
    expect(finished?.event, 'no output event is sent').to.equal('command');
    expect(finished?.data).to.deep.include({ status: 'completed', stdout: '' });
    world.commit((j) => j.task('task.updated', 'task-2', 'Fix the parser', 'in_progress'));
    const next = snapshotOf(await sse.next());
    expect(next.commands.find((c) => c.commandId === 'e3')).to.deep.include({ stdout: '' });
    await sse.cancel();
    expect((await world.get(log('t1.0'), 'owner')).status).to.equal(404);
  });

  it('AP-2: a finished run streams its snapshot and end at once', async () => {
    world = await start({ journal: activityScenario(new ActivityJournal(RUN_ID, SESSION, ORG)).completed(), status: 'completed' });
    const events = await new SseReader(await world.get(ACTIVITY, 'owner')).rest();
    expect(events.map((e) => e.event)).to.deep.equal(['snapshot', 'end']);
    expect(events[1]!.data).to.deep.equal({ status: 'completed' });
  });

  it('AP-3: a reconnect is a fresh snapshot: finished commands from the journal, a running one\'s output from the bus\'s retained frames', async () => {
    world = await start();
    world.bus.publish(frame(1, 'command_started', { commandId: 'e2', source: 'engine', command: 'pytest -q tests/test_parser.py', startedAt: 9 }));
    world.bus.publish(frame(2, 'command_output', { commandId: 'e2', stream: 'stdout', text: 'collecting 3 items\n' }));
    world.bus.publish(frame(3, 'command_output', { commandId: 'e1', stream: 'stdout', text: 'stale live output' }));
    const sse = new SseReader(await world.get(ACTIVITY, 'owner'));
    const view = snapshotOf(await sse.next());
    const byId = Object.fromEntries(view.commands.map((c) => [c.commandId, c]));
    expect(byId['e2']).to.deep.include({ status: 'running', stdout: 'collecting 3 items\n' });
    expect(byId['e1']).to.deep.include({ status: 'failed', stdout: '1 failed\n' });
    expect(byId['t1.0']).to.deep.include({ status: 'completed', stdout: 'hi\n', hasLog: true });
    await sse.cancel();
  });

  it('AP-4: a second client of the same journal and bus gets the identical snapshot', async () => {
    world = await start();
    world.bus.publish(frame(1, 'command_output', { commandId: 'e2', stream: 'stdout', text: 'live' }));
    const one = new SseReader(await world.get(ACTIVITY, 'owner'));
    const two = new SseReader(await world.get(ACTIVITY, 'owner'));
    const [a, b] = [snapshotOf(await one.next()), snapshotOf(await two.next())];
    expect(a).to.deep.equal(b);
    expect(a.commands.find((c) => c.commandId === 'e2')?.stdout).to.equal('live');
    await Promise.all([one.cancel(), two.cancel()]);
  });

  it('AP-5: a command\'s full log as text/plain, inline or sealed in the content store; 404 when it has none', async () => {
    world = await start();
    const inline = await world.get(log('t1.0'), 'owner');
    expect(inline.status).to.equal(200);
    expect(inline.headers.get('content-type')).to.equal('text/plain; charset=utf-8');
    expect(await inline.text()).to.equal('hi\n');

    const full = `${'line\n'.repeat(5_000)}token=••••••\n`;
    const sealed = await sealRunContent(world.cipher, world.blobs, world.run, Buffer.from(full));
    world.commit((j) =>
      j.commandFinished({ commandId: 'e7', source: 'engine', command: 'make', log: { ref: sealed.ref, hash: `sha256:${'d'.repeat(64)}`, size: full.length } }),
    );
    const stored = await world.get(log('e7'), 'owner');
    expect(stored.status).to.equal(200);
    expect(await stored.text()).to.equal(full);

    expect((await world.get(log('unknown'), 'owner')).status).to.equal(404);
    expect((await world.get(log('e1'), 'owner')).status, 'a record without a log').to.equal(404);
    world.blobs.objects.clear();
    expect((await world.get(log('e7'), 'owner')).status, 'its content is gone').to.equal(404);
  });

  it('AP-6: anyone but the run\'s actor is told the run is not found, on both routes', async () => {
    world = await start();
    for (const who of ['participant', 'viewer', 'stranger']) {
      expect((await world.get(ACTIVITY, who)).status, `${who} activity`).to.equal(404);
      expect((await world.get(log('t1.0'), who)).status, `${who} log`).to.equal(404);
    }
    const reply = await world.get(ACTIVITY, 'owner');
    expect(reply.status).to.equal(200);
    await reply.body?.cancel();
  });

  it('AP-7: with the Labs flag off, both routes answer 404 ACTIVITY_PANEL_DISABLED', async () => {
    world = await start();
    world.panel.on = false;
    for (const path of [ACTIVITY, log('t1.0')]) {
      const reply = await world.get(path, 'owner');
      expect(reply.status, path).to.equal(404);
      expect(((await reply.json()) as { error: { code: string } }).error.code).to.equal('HTTP_AGENT_HARNESS_ACTIVITY_PANEL_DISABLED');
    }
  });

  it('AP-8: without an activity bus, snapshots come from the journal alone, refreshed on each commit', async () => {
    world = await start({ bus: false });
    const sse = new SseReader(await world.get(ACTIVITY, 'owner'));
    const first = snapshotOf(await sse.next());
    expect(first.commands.find((c) => c.commandId === 'e2')).to.deep.include({ status: 'running', stdout: 'collecting' });
    world.commit((j) => j.engine('output', ' 3 items\n', { callId: 'e2', stream: 'stdout', truncated: false }));
    expect(snapshotOf(await sse.next()).commands.find((c) => c.commandId === 'e2')?.stdout).to.equal('collecting 3 items\n');
    world.run.status = 'failed';
    world.commit((j) => j.failed());
    expect(snapshotOf(await sse.next()).status).to.equal('failed');
    expect((await sse.next())?.event).to.equal('end');
  });

  it('AP-8: a bus that fails degrades to journal snapshots, without an error', async () => {
    world = await start();
    world.bus.failing = new Error('redis down');
    const sse = new SseReader(await world.get(ACTIVITY, 'owner'));
    expect(snapshotOf(await sse.next()).commands).to.have.length(3);
    world.commit((j) => j.task('task.updated', 'task-2', 'Fix the parser', 'completed'));
    expect(snapshotOf(await sse.next()).tasks.map((t) => t.status)).to.deep.equal(['completed', 'completed']);
    await sse.cancel();
  });

  it('the built-in assistant\'s conversation path serves the same panel', async () => {
    world = await start();
    const reply = await world.get(`/conversations/${SESSION}/runs/${RUN_ID}/activity`, 'owner');
    const sse = new SseReader(reply);
    expect(snapshotOf(await sse.next()).runId).to.equal(RUN_ID);
    await sse.cancel();
    expect(await (await world.get(`/conversations/${SESSION}/runs/${RUN_ID}/commands/t1.0/log`, 'owner')).text()).to.equal('hi\n');
  });

  it("ACTRESULTS: the snapshot carries a step's line and fingerprint; its result is read when opened, by the owner only", async () => {
    const journal = activityScenario(new ActivityJournal(RUN_ID, SESSION, ORG));
    journal.add('x.pipeshub.tool.result_presented', {
      callId: 'c-send',
      status: 'completed',
      content: '{"ok":true,"ts":"1729524660.000100"}',
      resultSummary: 'Message sent to #sales',
    });
    world = await start({ journal });
    const reply = await world.get(ACTIVITY, 'owner');
    const sse = new SseReader(reply);
    const first = await sse.next();
    expect(JSON.stringify(first?.data), 'no result in the snapshot').to.not.include('1729524660.000100');
    const send = snapshotOf(first).tools.find((t) => t.toolCallId === 'c-send')!;
    expect(send).to.deep.include({ outcome: { kind: 'summary', text: 'Message sent to #sales' }, approvalAnswer: 'allowed' });
    expect(send.detail).to.be.a('string');
    await sse.cancel();

    for (const path of [step('c-send'), `/conversations/${SESSION}/runs/${RUN_ID}/steps/c-send`]) {
      const opened = await world.get(path, 'owner');
      expect(opened.status, path).to.equal(200);
      expect(opened.headers.get('cache-control')).to.equal('private, no-store');
      const body = (await opened.json()) as { step: Record<string, unknown> };
      expect(body.step).to.deep.include({
        toolCallId: 'c-send',
        fingerprint: send.detail,
        resultSummary: 'Message sent to #sales',
        resultPreview: '{"ok":true,"ts":"1729524660.000100"}',
      });
    }
    for (const who of ['participant', 'viewer', 'stranger']) {
      const refused = await world.get(step('c-send'), who);
      expect(refused.status, who).to.equal(404);
      expect(await refused.text(), who).to.not.include('Message sent');
    }
    expect((await world.get(step('c-missing'), 'owner')).status, 'no such step').to.equal(404);
    expect((await world.get(step('bad%20id'), 'owner')).status, 'a malformed id').to.equal(400);
    world.panel.on = false;
    expect((await world.get(step('c-send'), 'owner')).status, 'the panel is off').to.equal(404);
  });
});
