import { describe, it, expect, vi } from 'vitest';
import { createAGUIEventHandler, type AGUIStreamTracking } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import type { SSEEvent } from '@/lib/api';

function frame(type: string, fields: Record<string, unknown> = {}): SSEEvent {
  return { event: type, data: { type, ...fields } };
}

function makeCallbacks(): {
  callbacks: StreamMessageCallbacks;
  spies: Record<string, ReturnType<typeof vi.fn>>;
} {
  const spies = {
    onConnected: vi.fn(),
    onStatus: vi.fn(),
    onChunk: vi.fn(),
    onComplete: vi.fn(),
    onArtifact: vi.fn(),
    onAskUserQuestion: vi.fn(),
    onReasoning: vi.fn(),
    onParts: vi.fn(),
    onError: vi.fn(),
  };
  return { callbacks: spies as unknown as StreamMessageCallbacks, spies };
}

describe('createAGUIEventHandler', () => {
  it('accumulates TEXT_MESSAGE_CONTENT deltas and forwards them via onChunk', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'Hello, ' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'world!' }));

    expect(spies.onChunk).toHaveBeenCalledTimes(2);
    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: 'world!',
      accumulated: 'Hello, world!',
      citations: [],
    });
  });

  it('resets the text buffer on a new TEXT_MESSAGE_START', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'first turn' }));
    handle(frame('TEXT_MESSAGE_END'));
    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'second' }));

    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: 'second',
      accumulated: 'second',
      citations: [],
    });
  });

  it('ignores TEXT_MESSAGE_CONTENT with an empty/missing delta', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', {}));

    expect(spies.onChunk).not.toHaveBeenCalled();
  });

  it('applies STATE_DELTA citations/normalizedAnswer patches and re-emits an onChunk', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);
    const citations = [{ id: 'c1' }];

    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'The answer is 42.' }));
    handle(
      frame('STATE_DELTA', {
        delta: [
          { op: 'replace', path: '/citations', value: citations },
          { op: 'replace', path: '/normalizedAnswer', value: 'The answer is 42.' },
        ],
      }),
    );

    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: '',
      accumulated: 'The answer is 42.',
      citations,
    });
  });

  it('does not shrink the visible buffer when normalizedAnswer lags behind the raw stream', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'The answer is forty-two.' }));
    handle(
      frame('STATE_DELTA', {
        delta: [{ op: 'replace', path: '/normalizedAnswer', value: 'The answer is' }],
      }),
    );

    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: '',
      accumulated: 'The answer is forty-two.',
      citations: [],
    });
  });

  it('ignores STATE_DELTA ops with an unrecognized path', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('STATE_DELTA', { delta: [{ op: 'replace', path: '/unknown', value: 'x' }] }));

    expect(spies.onChunk).toHaveBeenLastCalledWith({ chunk: '', accumulated: '', citations: [] });
  });

  it('routes STATE_DELTA add /artifacts/- ops to onArtifact, not onChunk', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);
    const artifact = { fileName: 'report.pdf', mimeType: 'application/pdf', downloadUrl: '/x' };

    handle(frame('STATE_DELTA', { delta: [{ op: 'add', path: '/artifacts/-', value: artifact }] }));

    expect(spies.onArtifact).toHaveBeenCalledWith(artifact);
    // No text/citation ops in this frame -- must not fire a spurious onChunk.
    expect(spies.onChunk).not.toHaveBeenCalled();
  });

  it('splits a mixed STATE_DELTA into onArtifact for the add op and onChunk for the replace ops', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);
    const artifact = { fileName: 'report.pdf', mimeType: 'application/pdf', downloadUrl: '/x' };

    handle(
      frame('STATE_DELTA', {
        delta: [
          { op: 'add', path: '/artifacts/-', value: artifact },
          { op: 'replace', path: '/normalizedAnswer', value: 'done' },
        ],
      }),
    );

    expect(spies.onArtifact).toHaveBeenCalledWith(artifact);
    expect(spies.onChunk).toHaveBeenLastCalledWith({ chunk: '', accumulated: 'done', citations: [] });
  });

  it('forwards REASONING_MESSAGE_CONTENT deltas as onReasoning with done:false', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('REASONING_MESSAGE_CONTENT', { delta: 'thinking...' }));

    expect(spies.onReasoning).toHaveBeenCalledWith({ delta: 'thinking...', done: false });
  });

  it('marks the reasoning turn complete on REASONING_END', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('REASONING_END'));

    expect(spies.onReasoning).toHaveBeenCalledWith({ delta: '', done: true });
  });

  it('ignores an empty REASONING_MESSAGE_CONTENT delta', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('REASONING_MESSAGE_CONTENT', {}));

    expect(spies.onReasoning).not.toHaveBeenCalled();
  });

  it('surfaces sub-agent delegation via STEP_STARTED as an onStatus update', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('STEP_STARTED', { stepName: 'sub_agent:internal_exploration_agent' }));

    expect(spies.onStatus).toHaveBeenCalledWith({
      status: 'executing',
      message: 'Delegating to internal_exploration_agent...',
    });
  });

  it('ignores STEP_STARTED for non sub-agent steps', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('STEP_STARTED', { stepName: 'some_other_step' }));

    expect(spies.onStatus).not.toHaveBeenCalled();
  });

  it('surfaces TOOL_CALL_START as an executing status message', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { toolCallName: 'jira_search' }));

    expect(spies.onStatus).toHaveBeenCalledWith({ status: 'executing', message: 'Using jira_search...' });
  });

  it('routes CUSTOM conversation_created to onConnected', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('CUSTOM', { name: 'conversation_created', value: { conversationId: 'c1' } }));

    expect(spies.onConnected).toHaveBeenCalledWith({ conversationId: 'c1' });
  });

  it('routes CUSTOM ask_user_question to onAskUserQuestion', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);
    const toolData = { name: 'ask_user_question', questions: [] };

    handle(frame('CUSTOM', { name: 'ask_user_question', value: { status: 'success', toolData } }));

    expect(spies.onAskUserQuestion).toHaveBeenCalledWith({ status: 'success', toolData });
  });

  it('routes CUSTOM artifact to onArtifact', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);
    const artifact = { fileName: 'report.pdf', mimeType: 'application/pdf', downloadUrl: '/x' };

    handle(frame('CUSTOM', { name: 'artifact', value: artifact }));

    expect(spies.onArtifact).toHaveBeenCalledWith(artifact);
  });

  it('ignores an unrecognized CUSTOM name', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('CUSTOM', { name: 'something_else', value: {} }));

    expect(spies.onConnected).not.toHaveBeenCalled();
    expect(spies.onAskUserQuestion).not.toHaveBeenCalled();
    expect(spies.onArtifact).not.toHaveBeenCalled();
  });

  it('calls onComplete and flips tracking.receivedComplete on a root RUN_FINISHED', () => {
    const { callbacks, spies } = makeCallbacks();
    const tracking: AGUIStreamTracking = { receivedComplete: false };
    const handle = createAGUIEventHandler(callbacks, tracking);
    const result = { conversation: { id: 'c1' }, recordsUsed: 1 };

    handle(frame('RUN_FINISHED', { result }));

    expect(tracking.receivedComplete).toBe(true);
    expect(spies.onComplete).toHaveBeenCalledWith(result);
  });

  it('ignores a nested (sub-agent) RUN_FINISHED carrying a parentRunId', () => {
    const { callbacks, spies } = makeCallbacks();
    const tracking: AGUIStreamTracking = { receivedComplete: false };
    const handle = createAGUIEventHandler(callbacks, tracking);

    handle(frame('RUN_FINISHED', { parentRunId: 'root-run', result: { answer: 'nested' } }));

    expect(tracking.receivedComplete).toBe(false);
    expect(spies.onComplete).not.toHaveBeenCalled();
  });

  it('does not call onComplete when a root RUN_FINISHED carries no result', () => {
    const { callbacks, spies } = makeCallbacks();
    const tracking: AGUIStreamTracking = { receivedComplete: false };
    const handle = createAGUIEventHandler(callbacks, tracking);

    handle(frame('RUN_FINISHED', {}));

    expect(tracking.receivedComplete).toBe(true);
    expect(spies.onComplete).not.toHaveBeenCalled();
  });

  it('calls onError on a root RUN_ERROR', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_ERROR', { message: 'Agent crashed' }));

    expect(spies.onError).toHaveBeenCalledTimes(1);
    const errorArg = spies.onError.mock.calls[0][0] as Error;
    expect(errorArg.message).toBe('Agent crashed');
  });

  it('falls back to a generic message when RUN_ERROR carries no message', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_ERROR', {}));

    const errorArg = spies.onError.mock.calls[0][0] as Error;
    expect(errorArg.message).toBe('Stream ended with an error');
  });

  it('ignores a nested (sub-agent) RUN_ERROR carrying a parentRunId', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_ERROR', { parentRunId: 'root-run', message: 'tool crashed' }));

    expect(spies.onError).not.toHaveBeenCalled();
  });

  it('silently ignores unmapped event types (e.g. RUN_STARTED, STATE_SNAPSHOT)', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_STARTED', { runId: 'r1' }));
    handle(frame('STATE_SNAPSHOT', { snapshot: {} }));

    Object.values(spies).forEach((spy) => expect(spy).not.toHaveBeenCalled());
  });

  it('falls back to event.event when data is missing a type field', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle({ event: 'TEXT_MESSAGE_START', data: undefined });
    handle({ event: 'TEXT_MESSAGE_CONTENT', data: { delta: 'hi' } });

    expect(spies.onChunk).toHaveBeenCalledWith({ chunk: 'hi', accumulated: 'hi', citations: [] });
  });
});

describe('createAGUIEventHandler — live agent-activity parts timeline', () => {
  it('builds a text part from TEXT_MESSAGE_START/CONTENT and emits it via onParts', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Hel' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'lo' }));

    expect(spies.onParts).toHaveBeenLastCalledWith([
      { type: 'text', content: 'Hello', runId: 'root' },
    ]);
  });

  it('builds a reasoning part from REASONING_MESSAGE_START/CONTENT', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('REASONING_MESSAGE_START', { runId: 'root' }));
    handle(frame('REASONING_MESSAGE_CONTENT', { runId: 'root', delta: 'thinking...' }));

    expect(spies.onParts).toHaveBeenLastCalledWith([
      { type: 'reasoning', content: 'thinking...', runId: 'root' },
    ]);
  });

  it('builds a tool_call part across START/ARGS/RESULT with status and truncated preview', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));
    handle(frame('TOOL_CALL_ARGS', { runId: 'root', toolCallId: 'call-1', delta: '{"q":"bug"}' }));
    handle(frame('TOOL_CALL_RESULT', { runId: 'root', toolCallId: 'call-1', content: '3 issues', status: 'completed' }));

    expect(spies.onParts).toHaveBeenLastCalledWith([
      {
        type: 'tool_call',
        toolCallId: 'call-1',
        toolName: 'jira_search',
        status: 'completed',
        runId: 'root',
        args: '{"q":"bug"}',
        resultPreview: '3 issues',
      },
    ]);
  });

  it('builds tool_call part with argsSummary and resultSummary when present', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));
    handle(frame('TOOL_CALL_ARGS', {
      runId: 'root', toolCallId: 'call-1', delta: '{"q":"bug"}', argsSummary: "Searched for 'bug'",
    }));
    handle(frame('TOOL_CALL_RESULT', {
      runId: 'root', toolCallId: 'call-1', content: '3 issues', status: 'completed', resultSummary: 'Found 3 issues',
    }));

    const lastCall = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastCall[0].argsSummary).toBe("Searched for 'bug'");
    expect(lastCall[0].resultSummary).toBe('Found 3 issues');
  });

  it('tool_call part omits summaries when wire fields are absent', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));
    handle(frame('TOOL_CALL_ARGS', { runId: 'root', toolCallId: 'call-1', delta: '{"q":"bug"}' }));
    handle(frame('TOOL_CALL_RESULT', { runId: 'root', toolCallId: 'call-1', content: '3 issues', status: 'completed' }));

    expect(spies.onParts).toHaveBeenLastCalledWith([
      {
        type: 'tool_call',
        toolCallId: 'call-1',
        toolName: 'jira_search',
        status: 'completed',
        runId: 'root',
        args: '{"q":"bug"}',
        resultPreview: '3 issues',
      },
    ]);
    const lastCall = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastCall[0].argsSummary).toBeUndefined();
    expect(lastCall[0].resultSummary).toBeUndefined();
  });

  it('tool_call part ignores non-string summary values', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));
    handle(frame('TOOL_CALL_ARGS', { runId: 'root', toolCallId: 'call-1', delta: '{"q":"bug"}', argsSummary: 42 }));
    handle(frame('TOOL_CALL_RESULT', {
      runId: 'root', toolCallId: 'call-1', content: '3 issues', status: 'completed', resultSummary: null,
    }));

    const lastCall = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastCall[0].argsSummary).toBeUndefined();
    expect(lastCall[0].resultSummary).toBeUndefined();
  });

  it('defaults a missing TOOL_CALL_RESULT status to completed', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));
    handle(frame('TOOL_CALL_RESULT', { runId: 'root', toolCallId: 'call-1', content: 'done' }));

    const lastCall = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastCall[0].status).toBe('completed');
  });

  it('drops a TOOL_CALL_RESULT with no matching open tool call (no part created)', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_RESULT', { runId: 'root', toolCallId: 'no-such-call', content: 'orphaned' }));

    expect(spies.onParts).toHaveBeenLastCalledWith([]);
  });

  it('nests child run activity under a sub_agent part keyed by the preceding STEP_STARTED role', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_STARTED', { runId: 'root' }));
    handle(frame('STEP_STARTED', { runId: 'root', stepName: 'sub_agent:internal_exploration_agent' }));
    handle(frame('RUN_STARTED', { runId: 'child-1', parentRunId: 'root' }));
    handle(frame('TEXT_MESSAGE_START', { runId: 'child-1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'child-1', delta: 'delegate answer' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastParts).toEqual([
      {
        type: 'sub_agent',
        runId: 'child-1',
        roleName: 'internal_exploration_agent',
        status: 'running',
        parts: [{ type: 'text', content: 'delegate answer', runId: 'child-1' }],
      },
    ]);
  });

  it('closes the sub_agent container on a child RUN_FINISHED without affecting root parts', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_STARTED', { runId: 'root' }));
    handle(frame('STEP_STARTED', { runId: 'root', stepName: 'sub_agent:explorer' }));
    handle(frame('RUN_STARTED', { runId: 'child-1', parentRunId: 'root' }));
    handle(frame('RUN_FINISHED', { runId: 'child-1', parentRunId: 'root' }));
    handle(frame('TEXT_MESSAGE_START', { runId: 'root' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'root answer' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastParts).toEqual([
      { type: 'sub_agent', runId: 'child-1', roleName: 'explorer', status: 'completed', parts: [] },
      { type: 'text', content: 'root answer', runId: 'root' },
    ]);
    // A child RUN_FINISHED must not be mistaken for the stream's own completion.
    expect(spies.onComplete).not.toHaveBeenCalled();
  });

  it('emits a new array reference on every parts update', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'a' }));
    const first = spies.onParts.mock.calls.at(-1)?.[0];
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'b' }));
    const second = spies.onParts.mock.calls.at(-1)?.[0];

    expect(first).not.toBe(second);
  });
});

describe('createAGUIEventHandler — root-run guards (child events do not leak into main chat)', () => {
  function registerChildRun(handle: (event: SSEEvent) => void, childRunId = 'child-1'): void {
    handle(frame('RUN_STARTED', { runId: 'root' }));
    handle(frame('STEP_STARTED', { runId: 'root', stepName: 'sub_agent:explorer' }));
    handle(frame('RUN_STARTED', { runId: childRunId, parentRunId: 'root' }));
  }

  it('does not reset textBuffer on a child TEXT_MESSAGE_START', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'hello' }));
    registerChildRun(handle);
    handle(frame('TEXT_MESSAGE_START', { runId: 'child-1' }));

    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: ' world' }));

    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: ' world',
      accumulated: 'hello world',
      citations: [],
    });
  });

  it('does not call onChunk for a child TEXT_MESSAGE_CONTENT', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    registerChildRun(handle);
    handle(frame('TEXT_MESSAGE_START', { runId: 'child-1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'child-1', delta: 'delegate says hi' }));

    expect(spies.onChunk).not.toHaveBeenCalled();

    handle(frame('TEXT_MESSAGE_START', { runId: 'root' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'root says hi' }));

    expect(spies.onChunk).toHaveBeenCalledWith({
      chunk: 'root says hi',
      accumulated: 'root says hi',
      citations: [],
    });
  });

  it('does not call onReasoning for a child REASONING_MESSAGE_CONTENT', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    registerChildRun(handle);
    handle(frame('REASONING_MESSAGE_CONTENT', { runId: 'child-1', delta: 'child thinking' }));

    expect(spies.onReasoning).not.toHaveBeenCalled();

    handle(frame('REASONING_MESSAGE_CONTENT', { runId: 'root', delta: 'root thinking' }));

    expect(spies.onReasoning).toHaveBeenCalledWith({ delta: 'root thinking', done: false });
  });

  it('still surfaces child text in the parts timeline (accordion) while leaving onChunk untouched', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    registerChildRun(handle);
    handle(frame('TEXT_MESSAGE_START', { runId: 'child-1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'child-1', delta: 'delegate answer' }));

    expect(spies.onChunk).not.toHaveBeenCalled();

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastParts).toEqual([
      {
        type: 'sub_agent',
        runId: 'child-1',
        roleName: 'explorer',
        status: 'running',
        parts: [{ type: 'text', content: 'delegate answer', runId: 'child-1' }],
      },
    ]);
  });
});

describe('createAGUIEventHandler — dedup guard against duplicate events', () => {
  it('ignores a duplicate TEXT_MESSAGE_START carrying the same messageId', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'msg-1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'hello' }));
    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'msg-1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: ' again' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const textParts = lastParts.filter((part: { type: string }) => part.type === 'text');
    expect(textParts).toHaveLength(1);
    expect(textParts[0].content).toBe('hello again');
  });

  it('ignores a duplicate TOOL_CALL_START carrying the same toolCallId', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'tc-1', toolCallName: 'jira_search' }));
    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'tc-1', toolCallName: 'jira_search' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const toolCallParts = lastParts.filter((part: { type: string }) => part.type === 'tool_call');
    expect(toolCallParts).toHaveLength(1);
  });
});

describe('createAGUIEventHandler — HEARTBEAT', () => {
  it('silently ignores HEARTBEAT events', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('HEARTBEAT', {}));

    Object.values(spies).forEach((spy) => expect(spy).not.toHaveBeenCalled());
  });
});

describe('createAGUIEventHandler — sub-agent status lifecycle', () => {
  function registerChildRun(handle: (event: SSEEvent) => void, childRunId = 'child-1'): void {
    handle(frame('RUN_STARTED', { runId: 'root' }));
    handle(frame('STEP_STARTED', { runId: 'root', stepName: 'sub_agent:explorer' }));
    handle(frame('RUN_STARTED', { runId: childRunId, parentRunId: 'root' }));
  }

  it('sets status to running when a sub-agent run starts', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    registerChildRun(handle);

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastParts[0].status).toBe('running');
  });

  it('sets status to completed on child RUN_FINISHED', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    registerChildRun(handle);
    handle(frame('RUN_FINISHED', { runId: 'child-1', parentRunId: 'root' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastParts[0].status).toBe('completed');
  });

  it('sets status to failed on child RUN_ERROR', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    registerChildRun(handle);
    handle(frame('RUN_ERROR', { runId: 'child-1', parentRunId: 'root', message: 'tool crashed' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    expect(lastParts[0].status).toBe('failed');
  });
});
