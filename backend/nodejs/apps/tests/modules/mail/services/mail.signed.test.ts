import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { MailProducer } from '../../../../src/modules/mail/services/mail.producer';
import { MailConsumer } from '../../../../src/modules/mail/services/mail.consumer';
import { MailEventType } from '../../../../src/modules/mail/types/mail-event.types';
import { forOrg, getCurrentOrgId } from '../../../../src/libs/context/orgContext';
import {
  configureEnvelope,
  deriveKey,
  runWithEnvelope,
  signMessage,
} from '../../../../src/libs/context/envelope';
import { BrokerTopic } from '../../../../src/libs/types/messaging.types';
import { setUserRepository } from '../../../../src/libs/repositories/user';

// A queued mail (an invite, an alert) must reach a consumer that enforces the signed envelope: with ENVELOPE_SIGN on it
// was published in a shape that cannot be signed, and the consumer dropped it as a stripped message.
describe('mail/services/mail.signed', () => {
  const org = '6b0800000000000000000001';
  const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() } as any;
  let savedSign: string | undefined;
  const wire: Array<Record<string, unknown>> = [];
  const broker = {
    isConnected: () => true,
    connect: async () => {},
    publish: async (topic: string, m: { value: Record<string, unknown> }) => {
      wire.push(JSON.parse(JSON.stringify(signMessage(topic, { ...m.value }))));
    },
  };
  const job = (eventId?: string) => ({
    mail: {
      emailTemplateType: 'appuserInvite',
      sendEmailTo: ['new.person@example.com'],
      subject: 'You are invited',
      templateData: {},
      orgId: org,
    },
    orgId: org,
    ...(eventId ? { eventId } : {}),
  });

  // The users the store knows, by org: a link's user and address come from here, never from the job.
  const usersByOrg: Record<string, Record<string, string>> = {
    [org]: { '6b08000000000000000000a1': 'new.person@example.com' },
    '6b0800000000000000000002': { '6b08000000000000000000b1': 'victim@b.example' },
  };

  /** The real MailConsumer, behind the envelope check the Redis Streams consumer runs. */
  const consume = async (messages: Array<Record<string, unknown>>) => {
    setUserRepository({
      getLive: async (o: string, id: string) => (usersByOrg[o]?.[id] ? { _id: id, orgId: o, email: usersByOrg[o][id] } : null),
    } as never);
    const sent: Array<{ to: string; org?: string }> = [];
    const sender = {
      getSmtpConfig: sinon.stub().returns({ host: 'mailpit', port: 1025, fromEmail: 'no-reply@example.com' }),
      send: sinon.stub().callsFake(async (mail: any) => {
        sent.push({ to: mail.sendEmailTo[0], org: getCurrentOrgId() });
        return { status: 'sent' };
      }),
    };
    let cb: ((m: { value: unknown }) => Promise<void>) | undefined;
    const mailBroker = { isConnected: () => true, consume: async (f: typeof cb) => { cb = f; } };
    const consumer = new MailConsumer(
      mailBroker as never,
      logger,
      sender as never,
      { start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves() } as never,
      () => ({ frontendUrl: 'http://app.test', scopedJwtSecret: 's' }) as never,
    );
    await consumer.consume(async () => {});
    const refused: string[] = [];
    for (const m of messages) {
      try {
        await runWithEnvelope(BrokerTopic.MAIL_EVENTS, m, (v) => cb!({ value: v }), 'mail-consumer-group');
      } catch (e) {
        refused.push((e as Error).message);
      }
    }
    return { sent, refused };
  };

  beforeEach(() => {
    savedSign = process.env.ENVELOPE_SIGN;
    wire.length = 0;
  });

  afterEach(() => {
    configureEnvelope(null);
    setUserRepository();
    if (savedSign === undefined) delete process.env.ENVELOPE_SIGN;
    else process.env.ENVELOPE_SIGN = savedSign;
  });

  it('with signing on, a mail queued in an org is signed under it and sent once by the enforcing consumer', async () => {
    process.env.ENVELOPE_SIGN = 'true';
    const key = deriveKey('mail-signed-test');
    configureEnvelope(async () => [key], null, key);

    await forOrg(org, 'test', () =>
      new MailProducer(broker as never, logger).publishEvent({
        eventType: MailEventType.SendMailEvent,
        timestamp: Date.now(),
        payload: job('mail-evt-1'),
      } as never),
    );

    expect(wire).to.have.length(1);
    expect((wire[0].ctx as { orgId?: string }).orgId, 'signed under the org').to.equal(org);
    expect((wire[0].ctx as { eventId?: string }).eventId, 'keeps the job id').to.equal('mail-evt-1');
    const { sent, refused } = await consume(wire);
    expect(refused).to.deep.equal([]);
    expect(sent).to.deep.equal([{ to: 'new.person@example.com', org }]);
  });

  it('with signing off, the job goes flat as before and is still sent', async () => {
    process.env.ENVELOPE_SIGN = 'false';

    await forOrg(org, 'test', () =>
      new MailProducer(broker as never, logger).publishEvent({
        eventType: MailEventType.SendMailEvent,
        timestamp: Date.now(),
        payload: job(),
      } as never),
    );

    expect(wire[0]).to.have.property('mail');
    expect(wire[0]).to.not.have.property('ctx');
    const { sent } = await consume(wire);
    expect(sent.map((s) => s.to)).to.deep.equal(['new.person@example.com']);
  });

  describe('a writer to the broker', () => {
    const orgB = '6b0800000000000000000002';
    const victimB = { userId: '6b08000000000000000000b1', orgId: orgB, email: 'victim@b.example' };
    const signedJob = async () => {
      process.env.ENVELOPE_SIGN = 'true';
      const key = deriveKey('mail-signed-test');
      configureEnvelope(async () => [key], null, key);
      await forOrg(org, 'test', () =>
        new MailProducer(broker as never, logger).publishEvent({
          eventType: MailEventType.SendMailEvent,
          timestamp: Date.now(),
          payload: job('mail-evt-2'),
        } as never),
      );
      return wire[0];
    };

    it('cannot add a top-level job to a signed message: the signed job is sent, the added one never', async () => {
      const genuine = await signedJob();
      const forged = {
        ...genuine,
        mail: { emailTemplateType: 'appuserInvite', sendEmailTo: ['attacker@evil.example'], subject: 'x', templateData: {} },
        passwordResetLinkFor: victimB,
      };

      const { sent } = await consume([forged]);

      expect(sent.map((s) => s.to)).to.deep.equal(['new.person@example.com']);
    });

    it('cannot hang a top-level job on a validly signed message that carries none: nothing is sent', async () => {
      process.env.ENVELOPE_SIGN = 'true';
      const key = deriveKey('mail-signed-test');
      configureEnvelope(async () => [key], null, key);
      // Any validly signed message on the topic whose payload is not a job (here: only the org).
      const signedOther = forOrg(org, 'test', () =>
        JSON.parse(JSON.stringify(signMessage(BrokerTopic.MAIL_EVENTS, { payload: { orgId: org } }))),
      );
      const forged = {
        ...signedOther,
        mail: { emailTemplateType: 'appuserInvite', sendEmailTo: ['attacker@evil.example'], subject: 'x', templateData: {} },
        passwordResetLinkFor: victimB,
      };

      const { sent } = await consume([forged]);

      expect(sent).to.deep.equal([]);
    });

    it('gets no set-password link for another org, nor one addressed to anyone else (unsigned path too)', async () => {
      process.env.ENVELOPE_SIGN = 'false';
      const otherOrg = { ...job(), passwordResetLinkFor: victimB };
      // The link's user is real, but the mail goes to someone else.
      const otherTo = {
        ...job(),
        mail: { ...job().mail, sendEmailTo: ['someone.else@example.com'] },
        passwordResetLinkFor: { userId: '6b08000000000000000000a1', orgId: org, email: 'someone.else@example.com' },
      };

      const { sent } = await consume([otherOrg, otherTo]);

      expect(sent, 'neither job is sent').to.deep.equal([]);
    });

    it('gets no set-password link copied to anyone by CC, with or without an org on the job (unsigned path)', async () => {
      process.env.ENVELOPE_SIGN = 'false';
      const cc = (withOrg: boolean) => {
        const { orgId: _o, ...noOrg } = job();
        const base = withOrg ? job() : noOrg;
        return {
          ...base,
          mail: { ...base.mail, sendEmailTo: [victimB.email], sendCcTo: ['attacker@evil.example'] },
          passwordResetLinkFor: victimB,
        };
      };

      const { sent } = await consume([cc(true), cc(false)]);

      expect(sent, 'nothing is sent, to the victim or the attacker').to.deep.equal([]);
    });

    it('gets no set-password link when no org is bound at all', async () => {
      process.env.ENVELOPE_SIGN = 'false';
      const { orgId: _o, ...noOrg } = job();
      const unbound = {
        ...noOrg,
        mail: { ...noOrg.mail, sendEmailTo: [victimB.email] },
        passwordResetLinkFor: victimB,
      };

      const { sent } = await consume([unbound]);

      expect(sent).to.deep.equal([]);
    });

    it("gets no link by naming its own address as the user's (the job is not proof of the address)", async () => {
      process.env.ENVELOPE_SIGN = 'false';
      const victimOrgJob = (to: string) => ({
        mail: { emailTemplateType: 'appuserInvite', sendEmailTo: [to], subject: 'x', templateData: {} },
        orgId: victimB.orgId,
        passwordResetLinkFor: { ...victimB, email: to },
      });

      const { sent } = await consume([
        victimOrgJob('attacker@evil.example'),
        victimOrgJob('victim@b.example, attacker@evil.example'),
      ]);

      expect(sent).to.deep.equal([]);
    });

    it("a link for a user the org does not have (or no longer has) is refused", async () => {
      process.env.ENVELOPE_SIGN = 'false';
      const ghost = {
        ...job(),
        mail: { ...job().mail, sendEmailTo: ['ghost@example.com'] },
        passwordResetLinkFor: { userId: '6b08000000000000000000ff', orgId: org, email: 'ghost@example.com' },
      };

      const { sent } = await consume([ghost]);

      expect(sent).to.deep.equal([]);
    });

    it('a genuine invite still gets its set-password link', async () => {
      process.env.ENVELOPE_SIGN = 'false';
      const invite = {
        ...job(),
        passwordResetLinkFor: { userId: '6b08000000000000000000a1', orgId: org, email: 'new.person@example.com' },
      };

      const { sent } = await consume([invite]);

      expect(sent.map((s) => s.to)).to.deep.equal(['new.person@example.com']);
    });
  });
});
