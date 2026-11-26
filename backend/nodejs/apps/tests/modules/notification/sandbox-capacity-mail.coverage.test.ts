import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as fs from 'fs';
import * as path from 'path';
import {
  buildSandboxCapacityMail,
  footerNoteFor,
} from '../../../src/modules/notification/utils/sandbox-capacity-mail';
import { parseSandboxCapacityAlert } from '../../../src/modules/notification/utils/sandbox-capacity-alert';
import {
  SANDBOX_CAPACITY_DEFAULT_FOOTER,
  isSandboxCapacityAlertData,
  sandboxCapacityAlertText,
} from '../../../src/modules/mail/utils/emailTemplates';
import { PlatformAlertHandler } from '../../../src/modules/notification/service/platform-alert.handler';
import { NotificationService } from '../../../src/modules/notification/service/notification.service';
import { NotificationContainer } from '../../../src/modules/notification/container/notification.container';
import { setNotificationRepository } from '../../../src/libs/repositories/notification';
import { setUserRepository } from '../../../src/libs/repositories/user';

const REPO = path.resolve(__dirname, '../../../../../..');
const read = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8');

const DOCS = 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts';

/** The email for an event with this payload, or null when the event is not accepted. */
const mailFor = (payload: Record<string, unknown>, top: Record<string, unknown> = {}) => {
  const type = payload.level === 'critical' ? 'SANDBOX_CAPACITY_CRITICAL' : 'SANDBOX_CAPACITY_WARNING';
  const parsed = parseSandboxCapacityAlert({
    audience: 'platformAdmins',
    type,
    ...top,
    payload: { v: 1, alertId: 'a1', ...payload },
  });
  return parsed.ok ? buildSandboxCapacityMail(parsed.alert) : null;
};

describe('sandbox capacity mail: every wording branch is pinned', () => {
  describe('the subject per level', () => {
    const cases: Array<[string, string]> = [
      ['warning', 'PipesHub: code runs in chat are waiting for capacity'],
      ['critical', 'PipesHub: code runs in chat are being refused'],
    ];
    for (const [level, subject] of cases) {
      it(level, () => {
        expect(mailFor({ level })?.subject).to.equal(subject);
      });
    }
  });

  it('builds no email for a level that is not warning or critical', () => {
    for (const level of ['resolved', 'info', undefined, 2, 'WARNING']) {
      expect(mailFor({ level })).to.equal(null);
    }
  });

  describe('the action for each reason, on a host and on a sidecar', () => {
    const cases: Array<[unknown, string, string[]]> = [
      ['memory', 'host', ['Add memory to the host, or move a database to another machine.']],
      [
        'memory',
        'sidecar',
        ["Raise the code runner's memory (sandbox.dind.resources), or lower sandbox.capacity.maxTotal."],
      ],
      ['cpu', 'host', ['Add CPU cores to the host.']],
      ['cpu', 'sidecar', ["Raise the code runner's CPU (sandbox.dind.resources)."]],
      [
        'slots',
        'host',
        ['Raise the sandbox limit (SANDBOX_MAX_TOTAL) if the host has room; otherwise add memory.'],
      ],
      [
        'slots',
        'sidecar',
        ['Raise sandbox.capacity.maxTotal if the code runner has room; otherwise give it more memory.'],
      ],
      // No reason reads as the slot limit, as the publisher defaults it.
      [undefined, 'sidecar', ['Raise sandbox.capacity.maxTotal if the code runner has room; otherwise give it more memory.']],
    ];
    for (const [reason, source, actions] of cases) {
      it(`${String(reason)} / ${source}`, () => {
        const mail = mailFor({
          level: 'warning',
          reason,
          memorySource: source,
        });
        expect(mail?.templateData.actions).to.deep.equal(actions);
      });
    }
  });

  describe('the settings the actions name exist where an operator would set them', () => {
    it('SANDBOX_MAX_TOTAL is documented for compose', () => {
      expect(read('deployment/docker-compose/env.template')).to.match(/^# SANDBOX_MAX_TOTAL=/m);
      expect(read('deployment/docker-compose/ADVANCED_DEPLOYMENT.md')).to.contain('| `SANDBOX_MAX_TOTAL` |');
    });
    it('sandbox.dind.resources and sandbox.capacity.maxTotal are Helm values', () => {
      const values = read('deployment/helm/pipeshub-ai/values.yaml');
      expect(values).to.match(/^sandbox:\n(?:(?: {2}.*)?\n)*? {2}dind:\n(?:(?: {4}.*)?\n)*? {4}resources:/m);
      expect(values).to.match(/^sandbox:\n(?:(?: {2}.*)?\n)*? {2}capacity:\n(?:(?: {4}.*)?\n)*? {4}maxTotal:/m);
    });
  });

  describe('the intro for each level when numbers are missing', () => {
    const cases: Array<[string, Record<string, unknown>, string]> = [
      [
        'critical with no slot wait',
        { level: 'critical', runs: 10, refused: 2, refusedPercent: 20 },
        'In the last 30 minutes, 2 of 10 code runs in chat (20%) were refused for a free sandbox. ' +
          'Users saw an error instead of a result.',
      ],
      [
        'critical with a slot wait',
        { level: 'critical', runs: 10, refused: 2, refusedPercent: 20, slotWaitSeconds: 120 },
        'In the last 30 minutes, 2 of 10 code runs in chat (20%) were refused after waiting 120 s for a free sandbox. ' +
          'Users saw an error instead of a result.',
      ],
      [
        'critical with nothing but the level',
        { level: 'critical' },
        'In the last 30 minutes, 0 of 0 code runs in chat (0%) were refused for a free sandbox. ' +
          'Users saw an error instead of a result.',
      ],
      [
        'warning with no longest wait and no slot wait',
        { level: 'warning', windowMinutes: 15, runs: 9, waited: 3, busyPercent: 40 },
        'For 40% of the last 15 minutes, code runs in chat waited for a free sandbox. 3 of 9 runs waited. ' +
          'Users saw "waiting in queue" before their code ran.',
      ],
      [
        'warning with both',
        { level: 'warning', runs: 9, waited: 3, busyPercent: 40, longestWaitSeconds: 61, slotWaitSeconds: 120 },
        'For 40% of the last 30 minutes, code runs in chat waited for a free sandbox. 3 of 9 runs waited, ' +
          'and the longest wait was 61 s. Users saw "waiting in queue" before their code ran. ' +
          'Runs that wait longer than 120 s are refused.',
      ],
      [
        'warning with numbers that are not finite',
        { level: 'warning', runs: Number.NaN, waited: Infinity, busyPercent: '40', longestWaitSeconds: null },
        'For 0% of the last 30 minutes, code runs in chat waited for a free sandbox. 0 of 0 runs waited. ' +
          'Users saw "waiting in queue" before their code ran.',
      ],
    ];
    for (const [name, payload, intro] of cases) {
      it(name, () => {
        expect(mailFor(payload)?.templateData.intro).to.equal(intro);
      });
    }
  });

  describe('the stats rows', () => {
    it('leave out the host, the limit and memory when the event lacks them', () => {
      const mail = mailFor({ level: 'warning', host: '  ', limit: null });
      expect(mail?.templateData.stats.map((s) => s.label)).to.deep.equal(['Window', 'Runs', 'Waited', 'Refused']);
    });

    it('leave out the limit when only one of its numbers is there', () => {
      const mail = mailFor({
        level: 'warning',
        limit: { current: 4 },
        hostMemoryFreeGb: 3,
      });
      const labels = mail?.templateData.stats.map((s) => s.label);
      expect(labels).to.not.include('Runs allowed at once now / at most');
      expect(labels).to.not.include('Host memory free / total');
    });

    it('name the code runner memory on a sidecar and the host memory otherwise', () => {
      for (const [source, label] of [
        ['sidecar', 'Code runner memory free / total'],
        ['host', 'Host memory free / total'],
      ]) {
        const mail = mailFor({
          level: 'critical',
          memorySource: source,
          hostMemoryFreeGb: 1.5,
          hostMemoryTotalGb: 16,
          refused: 3,
          refusedPercent: 5,
          limit: { current: 2, ceiling: 6 },
        });
        expect(mail?.templateData.stats).to.deep.include({ label, value: '1.5 GB / 16 GB' });
        expect(mail?.templateData.stats).to.deep.include({ label: 'Refused', value: '3 (5%)' });
        expect(mail?.templateData.stats).to.deep.include({
          label: 'Runs allowed at once now / at most',
          value: '2 / 6',
        });
      }
    });
  });

  it('builds no email for a reason it does not know', () => {
    expect(mailFor({ level: 'warning', reason: 'unknown' })).to.equal(null);
  });

  describe('the heading, reason and link are built here, never taken from the event', () => {
    const forged = {
      title: 'Action required: sign in again',
      message: 'Sign in at http://evil.example/login',
      redirectLink: 'http://evil.example/login',
    };
    const forgedPayload = {
      reasonText: 'Re-verify at http://evil.example/login now.',
      indexingText: 'Visit http://evil.example',
    };

    it('the heading is the title of the level', () => {
      expect(mailFor({ level: 'warning', ...forgedPayload }, forged)?.templateData.heading).to.equal(
        'Code runs are waiting for capacity',
      );
      expect(mailFor({ level: 'critical' }, forged)?.templateData.heading).to.equal('Code runs are being refused');
    });

    const reasons: Array<[Record<string, unknown>, string]> = [
      [{ reason: 'memory' }, 'Free memory on the host was low, so new runs were held back to protect the databases and the app.'],
      [{ reason: 'memory', memorySource: 'sidecar' }, 'The code runner was short of memory, so new runs were held back.'],
      [{ reason: 'cpu' }, "The host's CPU stayed busy, so fewer runs were allowed at once."],
      [{ reason: 'cpu', memorySource: 'sidecar' }, "The code runner's CPU stayed busy, so fewer runs were allowed at once."],
      [{ reason: 'slots', limit: { ceiling: 6 } }, 'All 6 code-run slots were in use. The host still had free memory and CPU, so the slot limit was the bottleneck.'],
      [{ reason: 'slots', limit: { ceiling: 1 } }, 'The only code-run slot was in use. The host still had free memory and CPU, so the slot limit was the bottleneck.'],
      [{ reason: 'slots', limit: { ceiling: -1 } }, 'All code-run slots were in use. The host still had free memory and CPU, so the slot limit was the bottleneck.'],
    ];
    for (const [extra, expected] of reasons) {
      it(`reason ${JSON.stringify(extra)}`, () => {
        expect(mailFor({ level: 'warning', ...forgedPayload, ...extra }, forged)?.templateData.reasonText).to.equal(
          expected,
        );
      });
    }

    it('says what indexing did only from the time it slowed down, for memory on a host', () => {
      const since = '2026-11-16T10:13:40Z';
      const indexing = (extra: Record<string, unknown>) =>
        mailFor({ level: 'warning', ...forgedPayload, ...extra })?.templateData.indexingText;
      expect(indexing({ reason: 'memory', indexingSlowedSince: since })).to.equal(
        'Indexing was already slowed down from 10:13 UTC to give chat priority.',
      );
      expect(indexing({ reason: 'memory' })).to.equal(undefined);
      expect(indexing({ reason: 'memory', indexingSlowedSince: 'yesterday' })).to.equal(undefined);
      expect(indexing({ reason: 'cpu', indexingSlowedSince: since })).to.equal(undefined);
      expect(indexing({ reason: 'memory', indexingSlowedSince: since, sandboxMode: 'helm_dind' })).to.equal(undefined);
    });

    for (const link of ['http://example.com/x', 'https://evil.example', 'javascript:alert(1)', '', undefined]) {
      it(`the docs link is the guide whatever the event says (${String(link)})`, () => {
        expect(mailFor({ level: 'warning' }, { redirectLink: link })?.templateData.docsUrl).to.equal(DOCS);
      });
    }
  });

  describe('the footer says how often the email may come', () => {
    const lead =
      'Admins are notified in the app each time code runs are held back again; this email is sent at most ';
    const tail = ' for each level. It goes to each admin of this workspace.';
    const cases: Array<[unknown, string]> = [
      [86400, 'once a day'],
      [undefined, 'once a day'],
      [0, 'once a day'],
      [0.4, 'once a day'],
      [-5, 'once a day'],
      [Number.NaN, 'once a day'],
      ['3600', 'once a day'],
      [172800, 'once every 2 days'],
      [3600, 'once an hour'],
      [7200, 'once every 2 hours'],
      [60, 'once a minute'],
      [600, 'once every 10 minutes'],
      [1, 'once a second'],
      [90, 'once every 90 seconds'],
      [3601, 'once every 3601 seconds'],
      [59.6, 'once a minute'],
    ];
    for (const [cooldown, phrase] of cases) {
      it(`${String(cooldown)} -> ${phrase}`, () => {
        expect(footerNoteFor(cooldown)).to.equal(`${lead}${phrase}${tail}`);
      });
    }
    it('the template default for older jobs is the one-day footer', () => {
      expect(SANDBOX_CAPACITY_DEFAULT_FOOTER).to.equal(footerNoteFor(86400));
    });
  });
});

describe('sandbox capacity email template data checks', () => {
  const good = {
    heading: 'h',
    intro: 'i',
    reasonText: 'r',
    actions: ['a'],
    stats: [{ label: 'l', value: 'v' }],
    docsUrl: 'https://x',
  };
  const cases: Array<[string, unknown, boolean]> = [
    ['valid data', good, true],
    ['valid data with optional text', { ...good, indexingText: 'x', footerNote: 'f' }, true],
    ['null', null, false],
    ['a string', 'x', false],
    ['an action that is not text', { ...good, actions: [1] }, false],
    ['a stat that is null', { ...good, stats: [null] }, false],
    ['a stat value that is a number', { ...good, stats: [{ label: 'l', value: 1 }] }, false],
    ['an indexing text that is a number', { ...good, indexingText: 1 }, false],
    ['a footer that is a number', { ...good, footerNote: 1 }, false],
    ['no docs link', { ...good, docsUrl: undefined }, false],
  ];
  for (const [name, data, ok] of cases) {
    it(`${name} -> ${ok}`, () => {
      expect(isSandboxCapacityAlertData(data)).to.equal(ok);
    });
  }

  it('the plain text of an older job without a footer ends with the default footer', () => {
    const textBody = sandboxCapacityAlertText(good);
    expect(textBody).to.equal(
      'h\n\ni\n\nWhy runs were held back: r\n\nWhat you can do:\n- a\n- Read the load alerts guide: https://x\n\n' +
        `l: v\n\n${SANDBOX_CAPACITY_DEFAULT_FOOTER}\n`,
    );
  });

  it('the plain text says what indexing already did', () => {
    expect(sandboxCapacityAlertText({ ...good, indexingText: 'Indexing slowed down.' })).to.contain(
      'Already done: Indexing slowed down. Runs were still held back after that, so the machine is short of capacity for this load.',
    );
  });
});

describe('platform alert handler: errors that are not Error objects', () => {
  let logger: any;
  let handler: PlatformAlertHandler;
  let mailProducer: { publishEvent: sinon.SinonStub };
  const evt = () => ({
    audience: 'platformAdmins',
    type: 'SANDBOX_CAPACITY_WARNING',
    title: 't',
    message: 'm',
    payload: { v: 1, alertId: 'a1', level: 'warning' },
  });

  beforeEach(() => {
    logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
    mailProducer = { publishEvent: sinon.stub().resolves() };
    setNotificationRepository({
      insertMany: sinon.stub().callsFake(async (docs: any[]) => docs.map((d, i) => ({ ...d, _id: `n${i}` }))),
    } as never);
    handler = new PlatformAlertHandler(
      logger,
      sinon.createStubInstance(NotificationService) as unknown as NotificationService,
      mailProducer as never,
      sinon.stub().resolves([{ orgId: '507f1f77bcf86cd799439011', userIds: ['507f1f77bcf86cd799439012'] }]),
    );
  });

  afterEach(() => {
    setNotificationRepository();
    setUserRepository();
    sinon.restore();
  });

  it('logs a thrown string from the publish and carries on', async () => {
    setUserRepository({
      listLiveByIds: sinon.stub().resolves([{ _id: '507f1f77bcf86cd799439012', email: 'a@example.com' }]),
      serviceAccountIdsAmong: sinon.stub().resolves([]),
    } as never);
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- the handler must log a non-Error rejection
    mailProducer.publishEvent.callsFake(() => Promise.reject('plain string'));
    await handler.handle(evt());
    expect(logger.error.calledWithMatch('Platform alert email not queued', { error: 'plain string' })).to.be.true;
    expect(logger.info.calledWithMatch('Platform alert delivered', { emailQueued: 0 })).to.be.true;
  });

  it('logs a thrown string from the user lookup and queues nothing', async () => {
    setUserRepository({
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- the handler must log a non-Error rejection
      listLiveByIds: sinon.stub().callsFake(() => Promise.reject('lookup down')),
      serviceAccountIdsAmong: sinon.stub().resolves([]),
    } as never);
    await handler.handle(evt());
    expect(logger.error.calledWithMatch('Platform alert email not queued', { error: 'lookup down' })).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
  });
});

describe('notification container: the alert mail producer is closed on dispose', () => {
  afterEach(() => {
    (NotificationContainer as any).container = null;
    sinon.restore();
  });

  const fakeContainer = (producer: Record<string, sinon.SinonStub> | Error, consumerBound = false) => {
    const unbindAll = sinon.stub();
    return {
      unbindAll,
      isBound: (key: string) => key === 'MessageProducer' || (consumerBound && key === 'MessageConsumer'),
      get: (key: string) => {
        if (key === 'MessageConsumer') return { isConnected: () => false, disconnect: sinon.stub() };
        if (producer instanceof Error) throw producer;
        return producer;
      },
    };
  };

  it('disconnects a connected producer', async () => {
    const producer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() };
    const c = fakeContainer(producer, true);
    (NotificationContainer as any).container = c;
    await NotificationContainer.dispose();
    expect(producer.disconnect.calledOnce).to.be.true;
    expect(c.unbindAll.calledOnce).to.be.true;
    expect((NotificationContainer as any).container).to.equal(null);
  });

  it('leaves a producer that never connected alone', async () => {
    const producer = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub().resolves() };
    (NotificationContainer as any).container = fakeContainer(producer);
    await NotificationContainer.dispose();
    expect(producer.disconnect.called).to.be.false;
  });

  it('still unbinds when the producer disconnect fails', async () => {
    const producer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().rejects(new Error('x')) };
    const c = fakeContainer(producer);
    (NotificationContainer as any).container = c;
    await NotificationContainer.dispose();
    expect(c.unbindAll.calledOnce).to.be.true;
  });

  it('still unbinds when the producer cannot be resolved', async () => {
    const c = fakeContainer(new Error('not resolvable'));
    (NotificationContainer as any).container = c;
    await NotificationContainer.dispose();
    expect(c.unbindAll.calledOnce).to.be.true;
  });
});
