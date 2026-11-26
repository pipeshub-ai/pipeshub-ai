/**
 * R35SLACK-FU-3 (ADR-OWD-09 D-SLACK-ASK 5): a person replies in Slack while the thread's run is parked at a card. The
 * message joins the run as steering and is read once the run goes on; the bot's reply is the hand-off (what waits, the
 * conversation's link) worded as queued, never "Added to the running task." as if the run were working on it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  ChatTurnService,
  QUEUED_REPLY,
  QUEUED_WAITING_REPLY,
} from '../../../src/modules/agent-harness/application/chat-turns';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { createSlackAGUIEventHandler } from '../../../src/integrations/slack-bot/src/utils/agui-stream';
import { handOffText } from '../../../src/integrations/slack-bot/src/utils/hand-off';

const run = (over: Partial<HarnessRun>): HarnessRun =>
  ({
    runId: 'r1',
    sessionId: 's1',
    agentKey: 'a1',
    status: 'running',
    ...over,
  }) as HarnessRun;

function service(
  admitted: HarnessRun,
  waitingOn = sinon
    .stub()
    .resolves([
      {
        toolName: 'mcp_e2esaas_create_ticket',
        displayName: 'e2esaas · create_ticket',
      },
    ]),
) {
  const admissions = {
    submit: sinon
      .stub()
      .resolves({
        route: 'durable',
        admission: { run: admitted, queued: { messageSeq: 2 } },
      }),
  };
  const svc = new ChatTurnService({
    admissions: admissions as never,
    runs: {} as never,
    streams: {} as never,
    waitingOn,
  });
  return { svc, waitingOn };
}

const turn = (surface: 'slack' | 'mcp' | 'web') =>
  ({ surface, orgId: 'o', actorId: 'u' }) as never;

function finished(
  frames: Array<{ frame: Record<string, unknown> }>,
): Record<string, unknown> {
  const last = frames[frames.length - 1]!.frame;
  expect(last['type']).to.equal('RUN_FINISHED');
  return last['result'] as Record<string, unknown>;
}

describe('R35SLACK-FU-3: a reply in a thread whose run is parked at a card', () => {
  it('SLACKPARK-1: Slack is answered with the hand-off, worded as queued, naming what waits and linking the conversation', async () => {
    const { svc } = service(
      run({ status: 'waiting_approval', openInteractions: ['ix-1'] }),
    );
    const start = await svc.start(turn('slack'));
    expect(start.route).to.equal('queued');
    const result = finished(
      (start as { frames: Array<{ frame: Record<string, unknown> }> }).frames,
    );
    expect(result['meta']).to.deep.equal({
      awaiting: 'approval',
      queued: true,
      link: '/chat/?agentId=a1&conversationId=s1',
      waitingOn: [
        {
          toolName: 'mcp_e2esaas_create_ticket',
          displayName: 'e2esaas · create_ticket',
        },
      ],
    });
    const conversation = result['conversation'] as {
      status: string;
      messages: Array<{ content: string }>;
    };
    expect(conversation.status).to.equal('Waiting');
    expect(conversation.messages[0]!.content).to.equal(QUEUED_WAITING_REPLY);
  });

  it('SLACKPARK-2: an MCP client reads the same words; the web app (the owner, who sees the card) does not', async () => {
    const parked = run({ status: 'waiting_approval' });
    const mcp = finished(
      (
        (await service(parked).svc.start(turn('mcp'))) as never as {
          frames: never;
        }
      ).frames,
    );
    expect((mcp['meta'] as Record<string, unknown>)['queued']).to.equal(true);
    const web = finished(
      (
        (await service(parked).svc.start(turn('web'))) as never as {
          frames: never;
        }
      ).frames,
    );
    expect(web['meta']).to.equal(undefined);
    expect(
      (web['conversation'] as { messages: Array<{ content: string }> })
        .messages[0]!.content,
    ).to.equal(QUEUED_REPLY);
  });

  it('SLACKPARK-3: a working run keeps "Added to the running task."; what waits is not read for it', async () => {
    const { svc, waitingOn } = service(run({ status: 'running' }));
    const result = finished(
      ((await svc.start(turn('slack'))) as never as { frames: never }).frames,
    );
    expect(result['meta']).to.equal(undefined);
    expect(waitingOn.called).to.equal(false);
  });

  it('SLACKPARK-4: what waits cannot be read: the message is still queued and the hand-off names nothing', async () => {
    const { svc } = service(
      run({ status: 'waiting_approval' }),
      sinon.stub().rejects(new Error('journal down')),
    );
    const result = finished(
      ((await svc.start(turn('slack'))) as never as { frames: never }).frames,
    );
    expect(
      (result['meta'] as Record<string, unknown>)['waitingOn'],
    ).to.deep.equal([]);
  });

  it('SLACKPARK-5: the bot posts it as a hand-off, saying the message is added and read once the run goes on', async () => {
    const { svc } = service(run({ status: 'waiting_approval' }));
    const result = finished(
      ((await svc.start(turn('slack'))) as never as { frames: never }).frames,
    );
    const onHandOff = sinon.stub();
    const onComplete = sinon.stub();
    createSlackAGUIEventHandler({ onHandOff, onComplete })({
      event: 'RUN_FINISHED',
      data: { type: 'RUN_FINISHED', result },
    });
    expect(onComplete.called).to.equal(false);
    const handOff = onHandOff.firstCall.args[0];
    expect(handOff.queued).to.equal(true);
    expect(handOffText(handOff, 'https://pipeshub.example')).to.equal(
      'This is still waiting for an approval: *e2esaas · Create ticket*.\n' +
        '<https://pipeshub.example/chat/?agentId=a1&conversationId=s1|Open this conversation in PipesHub> to approve or ' +
        "decline it. I've added your message; it will be read once this goes on, and I'll post the answer here.",
    );
  });
});
