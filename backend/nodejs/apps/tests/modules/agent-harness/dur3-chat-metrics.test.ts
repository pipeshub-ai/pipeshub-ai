import 'reflect-metadata';
import { EventEmitter } from 'events';
import { expect } from 'chai';
import { Request, Response } from 'express';
import {
  legacyTurnMetrics,
  terminalOutcome,
} from '../../../src/modules/agent-harness/transport/legacy-turn-metrics';
import {
  ChatPath,
  ChatPathMetrics,
  ChatSurface,
  ChatTurnOutcome,
} from '../../../src/modules/agent-harness/ports/chat-path-metrics';
import { prometheusChatPathMetrics } from '../../../src/modules/agent-harness/adapters/telemetry/chat-path-metrics';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { frameAGUI } from '../../../src/modules/enterprise_search/utils/agui';

/**
 * DUR3 condition f: today's loop's turns are counted beside the durable
 * path's on the same series. The legacy side reads the frames Node itself
 * writes last on the stream routes; the durable side is covered by the
 * execution-store contract ("chat path metrics and mid-flight rollback").
 */

const complete = (conversation: Record<string, unknown>, agui = false): string =>
  agui
    ? frameAGUI('RUN_FINISHED', { result: { conversation } })
    : `event: complete\ndata: ${JSON.stringify({ conversation })}\n\n`;

type Turn = [ChatPath, ChatSurface, ChatTurnOutcome];

class FakeResponse extends EventEmitter {
  writableEnded = false;
  writableFinished = false;
  readonly written: string[] = [];
  write(chunk: string | Buffer): boolean {
    this.written.push(chunk.toString());
    return true;
  }
  end(): void {
    this.writableEnded = true;
    this.emit('close');
  }
  drop(): void {
    this.emit('close');
  }
}

const serve = (surface: ChatSurface = 'agent') => {
  const turns: Turn[] = [];
  const metrics: ChatPathMetrics = {
    turnEnded: (path, s, outcome) => turns.push([path, s, outcome]),
    claimed: () => undefined,
    commitReplayed: () => undefined,
    modelCharged: () => undefined,
    runCacheRead: () => undefined,
    runQuarantined: () => undefined,
    approvalsRequested: () => undefined,
  };
  const res = new FakeResponse();
  let nextCalled = false;
  legacyTurnMetrics(surface, metrics)({} as Request, res as unknown as Response, () => {
    nextCalled = true;
  });
  expect(nextCalled).to.equal(true);
  return { res, turns };
};

describe('DUR3: chat path metrics', () => {
  describe('the outcome of a legacy turn (terminalOutcome)', () => {
    const cases: Array<[string, string, ChatTurnOutcome | null]> = [
      ['an answer', complete({ status: 'Complete', messages: [{ messageType: 'bot_response', content: 'Done.' }] }), 'answered'],
      ['an answer, AG-UI', complete({ status: 'Complete', messages: [{ messageType: 'bot_response', content: 'Done.' }] }, true), 'answered'],
      ['a stop', complete({ status: 'Stopped', messages: [] }), 'cancelled'],
      ['a classified failure', complete({ status: 'Failed', messages: [] }), 'failed'],
      ['an error message', complete({ status: 'Complete', messages: [{ messageType: 'error', content: 'x' }] }), 'failed'],
      [
        'an ask-user-question card',
        complete({ status: 'Complete', messages: [{ messageType: 'bot_response', content: '', tools: [{ toolName: 'ask_user_question' }] }] }),
        'needs_input',
      ],
      ['an error frame', `event: error\ndata: {"error":"x"}\n\n`, 'failed'],
      ['an AG-UI error frame', frameAGUI('RUN_ERROR', { message: 'x', code: 'no_response' }), 'failed'],
      ['a delta', frameAGUI('TEXT_MESSAGE_CONTENT', { delta: 'hi' }), null],
    ];
    for (const [name, text, expected] of cases) {
      it(name, () => expect(terminalOutcome(text)).to.equal(expected));
    }
    it('the last terminal frame wins', () => {
      expect(terminalOutcome(`event: error\ndata: {}\n\n${complete({ status: 'Complete', messages: [] })}`)).to.equal('answered');
    });
  });

  describe('legacyTurnMetrics', () => {
    it('DUR3-F1: a finished stream is one legacy turn with the outcome it wrote last', () => {
      const { res, turns } = serve('assistant');
      res.write(frameAGUI('TEXT_MESSAGE_CONTENT', { delta: 'Done.' }));
      res.write(complete({ status: 'Complete', messages: [{ messageType: 'bot_response', content: 'Done.' }] }));
      res.end();
      expect(turns).to.deep.equal([['legacy', 'assistant', 'answered']]);
    });

    it('a browser that left mid-stream is disconnected', () => {
      const { res, turns } = serve();
      res.write(frameAGUI('TEXT_MESSAGE_CONTENT', { delta: 'Do' }));
      res.drop();
      expect(turns).to.deep.equal([['legacy', 'agent', 'disconnected']]);
    });

    it('a response that ended with no terminal frame failed', () => {
      const { res, turns } = serve();
      res.end();
      expect(turns).to.deep.equal([['legacy', 'agent', 'failed']]);
    });
  });

  describe('the Prometheus adapter', () => {
    it('DUR3-F1..F4: every series the rollout is judged by, labelled by path', async () => {
      prometheusChatPathMetrics.turnEnded('durable', 'agent', 'answered', 3);
      prometheusChatPathMetrics.turnEnded('legacy', 'agent', 'failed', 1);
      prometheusChatPathMetrics.claimed('expired_lease');
      prometheusChatPathMetrics.commitReplayed();
      prometheusChatPathMetrics.modelCharged(0.0125, 900);
      const text = await metricsBackend.serialize();
      expect(text).to.match(/pipeshub_agent_chat_turns_total\{path="durable",surface="agent",outcome="answered"\} \d+/);
      expect(text).to.match(/pipeshub_agent_chat_turns_total\{path="legacy",surface="agent",outcome="failed"\} \d+/);
      expect(text).to.include('pipeshub_agent_chat_turn_duration_seconds_bucket');
      expect(text).to.match(/pipeshub_agent_harness_run_claims_total\{from="expired_lease"\} \d+/);
      expect(text).to.match(/pipeshub_agent_harness_commit_replays_total \d+/);
      expect(text).to.match(/pipeshub_agent_harness_model_cost_usd_total [0-9.]+/);
      expect(text).to.match(/pipeshub_agent_harness_model_tokens_total \d+/);
    });
  });
});
