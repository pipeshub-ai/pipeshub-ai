import 'reflect-metadata';
import { expect } from 'chai';
import {
  finalTurnRow,
  type BotResponse,
} from '../../../src/integrations/slack-bot/src/helpers';

const row = (messageType: string, content = ''): BotResponse => ({ messageType, content });

describe('slack-bot finalTurnRow', () => {
  it('returns the answer the finished turn saved', () => {
    const answer = row('bot_response', 'this turn');

    expect(
      finalTurnRow([
        row('user_query', 'earlier question'),
        row('bot_response', 'earlier answer'),
        row('user_query', 'question'),
        row('tool_call'),
        answer,
      ]),
    ).to.equal(answer);
  });

  it('returns the error row of a failed turn, never an earlier answer', () => {
    const failure = row('error', 'The model is unavailable.');

    expect(
      finalTurnRow([
        row('user_query', 'earlier question'),
        row('bot_response', 'earlier answer'),
        row('user_query', 'question'),
        failure,
      ]),
    ).to.equal(failure);
  });

  it('skips rows after the answer that are not answers', () => {
    const answer = row('bot_response', 'this turn');

    expect(finalTurnRow([row('user_query', 'question'), answer, row('system')])).to.equal(answer);
  });

  it('returns null when the turn saved no answer or error row', () => {
    expect(finalTurnRow([row('user_query', 'question'), row('tool_call')])).to.equal(null);
    expect(finalTurnRow([])).to.equal(null);
  });
});
