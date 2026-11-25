/**
 * SKILLSHOW-FU-1: an answer saved before FIX-SKILLSHOW kept its engine frames without `skills`, so its timeline
 * still showed the chained command. The conversation read names the skills at read time (`withSavedSkills`), with
 * the same recognizer as a live line; nothing stored is rewritten.
 */
import { expect } from 'chai';
import { withSavedEngineLines as withSavedSkills } from '../../../../src/modules/agent-harness/projection/legacy-engine-lines';
import { attachMessages } from '../../../../src/modules/enterprise_search/utils/utils';

const CODEX_CHAIN =
  `/bin/bash -lc "cat /pipeshub/AGENTS.md && printf '\\n---PDF SKILL---\\n' && cat /skills/pdf/SKILL.md && ` +
  `printf '\\n---TOOLS INDEX---\\n' && cat /pipeshub/tools/INDEX.md"`;

/** An answer as FIX-SKILLSHOW's base saved it: the engine part's frames carry no `skills`. */
const savedBefore = () => ({
  _id: 'm1',
  sessionId: 's1',
  orgId: 'o1',
  seq: 2,
  messageType: 'bot_response',
  content: 'Done.',
  parts: [
    { type: 'text', content: 'Done.' },
    {
      type: 'engine_task',
      runId: 'run_1',
      taskId: 'task_1',
      frames: [
        { tool: 'code_task', taskId: 'task_1', kind: 'command', text: CODEX_CHAIN, data: { callId: 'c1' } },
        { tool: 'code_task', taskId: 'task_1', kind: 'command_done', text: '', data: { callId: 'c1', exitCode: 0 } },
        { tool: 'code_task', taskId: 'task_1', kind: 'tool', text: 'Skill', data: { callId: 'c2', args: '{"skill":"xlsx"}' } },
        { tool: 'code_task', taskId: 'task_1', kind: 'command', text: 'node make.js', data: { callId: 'c3' } },
      ],
    },
  ],
});

describe('SKILLSHOW-FU-1 saved answers name their skill reads', () => {
  it('a conversation read names the skill of a saved chained command and of a saved Skill call', () => {
    const [message] = attachMessages({ _id: 's1' }, [savedBefore()]).messages;
    const frames = message.parts[1].frames;
    expect(frames[0].data).to.deep.equal({ callId: 'c1', skills: ['pdf'] });
    expect(frames[0].text).to.equal(CODEX_CHAIN);
    expect(frames[2].data.skills).to.deep.equal(['xlsx']);
    expect(frames[1].data).to.deep.equal({ callId: 'c1', exitCode: 0 });
    expect(frames[3].data).to.deep.equal({ callId: 'c3' });
  });

  it('keeps what a line already names, and reads parts with no skill read as they were saved', () => {
    const named = savedBefore();
    const first = named.parts[1]?.frames?.[0] ?? expect.fail('the engine part\'s first line');
    first.data = { callId: 'c1', skills: ['docx'] } as never;
    expect(withSavedSkills(named.parts)[1]).to.have.nested.property('frames[0].data.skills[0]', 'docx');
    const plain = [{ type: 'engine_task', frames: [{ kind: 'command', text: 'ls', data: {} }] }, { type: 'text' }];
    expect(withSavedSkills(plain)).to.deep.equal(plain);
    expect(withSavedSkills(undefined)).to.equal(undefined);
  });

  it('does not rewrite the stored message', () => {
    const stored = savedBefore();
    attachMessages({ _id: 's1' }, [stored]);
    expect(stored.parts[1]?.frames?.[0]?.data).to.deep.equal({ callId: 'c1' });
  });

  it('leaves a message without parts untouched', () => {
    const [message] = attachMessages({ _id: 's1' }, [{ _id: 'q', seq: 1, content: 'hi' }]).messages;
    expect(message).to.deep.equal({ _id: 'q', content: 'hi' });
  });
});
