/**
 * SEC4-N1: what Node sends a participant who is not the run's actor goes
 * through the shared redactor: tool arguments by key name and shape, every
 * other string by shape, numbers and ordinary keys untouched.
 */
import { expect } from 'chai';
import { shapedForViewer, SECRET_MASK } from '../../../src/modules/agent-harness/domain/token-redaction';
import { assertNoCanary, VECTOR_EXAMPLES } from './support/canary-scan';

const PASSWORD = 'hunter2-not-a-shape';

describe('SEC4-N1: shapedForViewer', () => {
  for (const { id, secret } of VECTOR_EXAMPLES) {
    it(`masks ${id} wherever a message carries it`, () => {
      const message = {
        content: `answer with ${secret}`,
        parts: [
          { type: 'tool_call', args: JSON.stringify({ q: secret, password: PASSWORD }), argsSummary: `Search ${secret}`, resultPreview: `got ${secret}` },
          { type: 'approval', args: { token: PASSWORD, note: secret }, reason: { destination: secret } },
          { type: 'engine_task', frames: [{ kind: 'command', command: `curl -H ${secret}`, output: [secret] }] },
        ],
      };
      const shaped = shapedForViewer(message);
      assertNoCanary(id, shaped, { secrets: [secret, PASSWORD] });
      expect(JSON.stringify(shaped)).to.include(SECRET_MASK);
    });
  }

  it('masks a secret-named argument whatever its shape, keeps ordinary ones and every non-argument field', () => {
    const shaped = shapedForViewer({
      messageType: 'bot_response',
      authorId: 'u-1',
      tokens: 42,
      usage: { inputTokens: 10, outputTokens: 5 },
      parts: [{ type: 'tool_call', toolName: 'jira__create', args: '{"title":"deploy","apiKey":"short"}', status: 'completed' }],
    });
    expect(shaped).to.deep.equal({
      messageType: 'bot_response',
      authorId: 'u-1',
      tokens: 42,
      usage: { inputTokens: 10, outputTokens: 5 },
      parts: [{ type: 'tool_call', toolName: 'jira__create', args: `{"title":"deploy","apiKey":"${SECRET_MASK}"}`, status: 'completed' }],
    });
  });

  it('leaves a value without secrets as it was', () => {
    const plain = { content: 'You get 25 days.', parts: [{ type: 'text', content: 'You get 25 days.', isFinal: true }], n: null };
    expect(shapedForViewer(plain)).to.deep.equal(plain);
  });
});
