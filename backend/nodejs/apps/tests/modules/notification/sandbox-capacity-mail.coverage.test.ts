import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as fs from 'fs';
import * as path from 'path';
import {
  buildSandboxCapacityMail,
  footerNoteFor,
} from '../../../src/modules/notification/utils/sandbox-capacity-mail';
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

const event = {
  title: 'Code runs are waiting for capacity',
  message: 'the event message',
  redirectLink: 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts',
};

describe('sandbox capacity mail: every wording branch is pinned', () => {
  describe('the subject per level', () => {
    const cases: Array<[string, string]> = [
      ['warning', 'PipesHub: code runs in chat are waiting for capacity'],
      ['critical', 'PipesHub: code runs in chat are being refused'],
    ];
    for (const [level, subject] of cases) {
      it(`${level}`, () => {
        expect(buildSandboxCapacityMail(event, { level })?.subject).to.equal(subject);
      });
    }
  });

  it('builds no email for a level that is not warning or critical', () => {
    for (const level of ['resolved', 'info', undefined, 2, 'WARNING']) {
      expect(buildSandboxCapacityMail(event, { level })).to.equal(null);
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
      ['unknown', 'host', []],
      [undefined, 'sidecar', []],
    ];
    for (const [reason, source, actions] of cases) {
      it(`${String(reason)} / ${source}`, () => {
        const mail = buildSandboxCapacityMail(event, {
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
        expect(buildSandboxCapacityMail(event, payload)?.templateData.intro).to.equal(intro);
      });
    }
  });

  describe('the stats rows', () => {
    it('leave out the host, the limit and memory when the event lacks them', () => {
      const mail = buildSandboxCapacityMail(event, { level: 'warning', host: '  ', limit: null });
      expect(mail?.templateData.stats.map((s) => s.label)).to.deep.equal(['Window', 'Runs', 'Waited', 'Refused']);
    });

    it('leave out the limit when only one of its numbers is there', () => {
      const mail = buildSandboxCapacityMail(event, {
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
        const mail = buildSandboxCapacityMail(event, {
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

  describe('the reason text falls back to the event message, then to nothing', () => {
    const cases: Array<[Record<string, unknown>, Record<string, unknown>, string]> = [
      [event, { reasonText: 'All slots in use.' }, 'All slots in use.'],
      [event, { reasonText: ' ' }, 'the event message'],
      [{ title: 7, message: '' }, {}, ''],
    ];
    for (const [evt, extra, expected] of cases) {
      it(JSON.stringify(extra), () => {
        expect(buildSandboxCapacityMail(evt, { level: 'warning', ...extra })?.templateData.reasonText).to.equal(
          expected,
        );
      });
    }
    it('an event title that is not text gives an empty heading', () => {
      expect(buildSandboxCapacityMail({ title: 7 }, { level: 'warning' })?.templateData.heading).to.equal('');
    });
  });

  describe('the docs link keeps only web links', () => {
    const fallback = 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts';
    const cases: Array<[unknown, string]> = [
      ['http://example.com/x', 'http://example.com/x'],
      ['javascript:alert(1)', fallback],
      ['data:text/html,x', fallback],
      ['not a url', fallback],
      ['', fallback],
      [undefined, fallback],
    ];
    for (const [link, expected] of cases) {
      it(String(link), () => {
        expect(
          buildSandboxCapacityMail({ ...event, redirectLink: link }, { level: 'warning' })?.templateData.docsUrl,
        ).to.equal(expected);
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
    mailProducer.publishEvent.callsFake(() => Promise.reject('plain string'));
    await handler.handle(evt());
    expect(logger.error.calledWithMatch('Platform alert email not queued', { error: 'plain string' })).to.be.true;
    expect(logger.info.calledWithMatch('Platform alert delivered', { emailQueued: 0 })).to.be.true;
  });

  it('logs a thrown string from the user lookup and queues nothing', async () => {
    setUserRepository({
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
