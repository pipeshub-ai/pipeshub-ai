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
    onAnswerFinal: vi.fn(),
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

  it('adopts normalizedAnswer when rawLength covers the full textBuffer (ref-stripped text)', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START'));
    // Raw LLM output includes a ref citation pattern
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'Here is the answer [source](ref1)' }));
    // Backend normalizes: strips the unresolved ref, but reports the raw
    // buffer length so the frontend knows the normalization is current.
    handle(
      frame('STATE_DELTA', {
        delta: [
          { op: 'replace', path: '/normalizedAnswer', value: 'Here is the answer' },
          { op: 'replace', path: '/rawLength', value: 33 },
        ],
      }),
    );

    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: '',
      accumulated: 'Here is the answer',
      citations: [],
    });
  });

  it('strips the streaming Confidence trailer via rawLength even though TEXT_MESSAGE_CONTENT still carries it', () => {
    // AG-UI TEXT_MESSAGE_CONTENT is append-only (raw LLM tokens). The
    // backend strips `---\nConfidence: High` into STATE_DELTA.normalizedAnswer
    // and reports rawLength so we can adopt the shorter cleaned text.
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);
    const body = 'I found two Jira tickets.';

    handle(frame('TEXT_MESSAGE_START'));
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: body }));
    handle(
      frame('STATE_DELTA', {
        delta: [
          { op: 'replace', path: '/normalizedAnswer', value: body },
          { op: 'replace', path: '/rawLength', value: body.length },
        ],
      }),
    );

    const trailerParts = ['---\n', 'Confidence', ':', ' High'];
    let raw = body;
    for (const part of trailerParts) {
      raw += part;
      handle(frame('TEXT_MESSAGE_CONTENT', { delta: part }));
      handle(
        frame('STATE_DELTA', {
          delta: [
            { op: 'replace', path: '/normalizedAnswer', value: body },
            { op: 'replace', path: '/rawLength', value: raw.length },
            ...(part === ' High'
              ? [{ op: 'replace' as const, path: '/confidence', value: 'High' }]
              : []),
          ],
        }),
      );
    }

    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: '',
      accumulated: body,
      citations: [],
    });
  });

  it('keeps adopting normalizedAnswer after citation resolution lengthens the text', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START'));
    // Raw LLM output: 'Check this [source](ref1) ok'
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: 'Check this [source](ref1) ok' }));
    // Backend resolves the citation to a long URL, making normalizedAnswer LONGER
    const resolved = 'Check this [1](https://example.com/very/long/record/path/preview#blockIndex=5) ok';
    handle(
      frame('STATE_DELTA', {
        delta: [
          { op: 'replace', path: '/normalizedAnswer', value: resolved },
          { op: 'replace', path: '/rawLength', value: 28 },
        ],
      }),
    );
    // normalizedAnswer adopted (longer than raw)
    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: '',
      accumulated: resolved,
      citations: [],
    });

    // Next tokens arrive — textBuffer is now the longer resolved text + new delta
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: ' and more' }));
    handle(
      frame('STATE_DELTA', {
        delta: [
          { op: 'replace', path: '/normalizedAnswer', value: resolved + ' and more' },
          { op: 'replace', path: '/rawLength', value: 37 },
        ],
      }),
    );
    // Guard must still pass: rawLength (37) >= rawTextReceived (37)
    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: '',
      accumulated: resolved + ' and more',
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

    expect(spies.onStatus).toHaveBeenCalledWith({ status: 'executing', message: 'Using Jira Search...' });
  });

  it('restores Thinking status on TEXT_MESSAGE_END so the UI is not idle between narration and the next tool', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Regenerating the PDF.' }));
    spies.onStatus.mockClear();
    handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));

    expect(spies.onStatus).toHaveBeenCalledWith({ status: 'calling_llm', message: 'Thinking...' });
  });

  it('keeps TOOL_CALL_START status present-tense even when displayName is past-tense', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', {
      toolCallName: 'run_code',
      displayName: 'Ran code',
      toolCallId: 'call-1',
    }));

    expect(spies.onStatus).toHaveBeenCalledWith({ status: 'executing', message: 'Using Run Code...' });
  });

  it('restores Thinking status after TOOL_CALL_RESULT while waiting for the next LLM turn', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'run_code' }));
    spies.onStatus.mockClear();
    handle(frame('TOOL_CALL_RESULT', {
      runId: 'root',
      toolCallId: 'call-1',
      content: 'ok',
      status: 'completed',
    }));

    expect(spies.onStatus).toHaveBeenCalledWith({ status: 'calling_llm', message: 'Thinking...' });
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

  it('reports a progress STATE_SNAPSHOT as a status, not as the final answer', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('STATE_SNAPSHOT', { snapshot: { status: 'calling_llm' } }));

    expect(spies.onStatus).toHaveBeenCalledWith({ status: 'calling_llm', message: 'Thinking...' });
    expect(spies.onAnswerFinal).not.toHaveBeenCalled();
  });

  it('reports `snapshot.final` as the settled answer and emits no progress status', () => {
    // Guards the "Thinking…" watchdog in streaming.ts: this snapshot is what
    // retires it, and it arrives while Node still withholds RUN_FINISHED.
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('STATE_SNAPSHOT', { snapshot: { final: true, conversation: {} } }));

    expect(spies.onAnswerFinal).toHaveBeenCalledTimes(1);
    expect(spies.onStatus).not.toHaveBeenCalled();
  });

  it('reports the settled answer on a root RUN_FINISHED before completing', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_FINISHED', { runId: 'r1', result: { conversation: {} } }));

    expect(spies.onAnswerFinal).toHaveBeenCalledTimes(1);
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
  });

  it('does not report a settled answer on a child RUN_FINISHED', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_FINISHED', { runId: 'child', parentRunId: 'root' }));

    expect(spies.onAnswerFinal).not.toHaveBeenCalled();
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

describe('createAGUIEventHandler — settling narration text (duplicate-text fix)', () => {
  it('settles narration text and clears the answer buffer when REASONING_MESSAGE_START follows TEXT_MESSAGE_END on root run', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Let me check that.' }));
    handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));
    spies.onChunk.mockClear();

    handle(frame('REASONING_MESSAGE_START', { runId: 'root' }));

    // Buffer cleared — AnswerContent no longer mirrors the narration.
    expect(spies.onChunk).toHaveBeenCalledWith({ chunk: '', accumulated: '', citations: [] });
    // The text part is settled — filterRootParts can now show it in the timeline.
    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const textPart = lastParts.find((part: { type: string }) => part.type === 'text');
    expect(textPart).toMatchObject({ content: 'Let me check that.', settled: true });
  });

  it('settles narration text and clears the answer buffer when TOOL_CALL_START follows TEXT_MESSAGE_END on root run', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Searching Jira.' }));
    handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const textPart = lastParts.find((part: { type: string }) => part.type === 'text');
    expect(textPart).toMatchObject({ content: 'Searching Jira.', settled: true });
  });

  it('settles narration text and clears the answer buffer when a new root text turn starts', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'First turn.' }));
    handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));
    spies.onChunk.mockClear();

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm2' }));

    // Buffer cleared before the new turn's own deltas arrive.
    expect(spies.onChunk).toHaveBeenCalledWith({ chunk: '', accumulated: '', citations: [] });
    const partsAfterStart = spies.onParts.mock.calls.at(-1)?.[0];
    const firstTextPart = partsAfterStart.find(
      (part: { content?: string }) => part.content === 'First turn.',
    );
    expect(firstTextPart).toMatchObject({ settled: true });
  });

  it('does not settle the text part for a duplicate TEXT_MESSAGE_START (same messageId)', () => {
    // Guards against a regression: settling on a duplicate replay would
    // replace the open part's object reference and orphan the reference
    // `handleTextContent` holds for it, silently dropping later deltas
    // (see the sibling dedup-guard test for that content-accumulation check).
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'hello' }));
    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: ' again' }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const textParts = lastParts.filter((part: { type: string }) => part.type === 'text');
    expect(textParts).toHaveLength(1);
    expect(textParts[0]).toMatchObject({ content: 'hello again' });
    expect(textParts[0].settled).toBeUndefined();
  });

  it('does not settle a child (sub-agent) text turn when a reasoning block starts on root', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('RUN_STARTED', { runId: 'root' }));
    handle(frame('STEP_STARTED', { runId: 'root', stepName: 'sub_agent:explorer' }));
    handle(frame('RUN_STARTED', { runId: 'child-1', parentRunId: 'root' }));
    handle(frame('TEXT_MESSAGE_START', { runId: 'child-1', messageId: 'c1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'child-1', delta: 'delegate narration' }));

    handle(frame('REASONING_MESSAGE_START', { runId: 'root' }));

    // The root answer buffer was never touched by the child's text, so no
    // clearing onChunk should fire here.
    expect(spies.onChunk).not.toHaveBeenCalled();
    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const subAgentPart = lastParts.find((part: { type: string }) => part.type === 'sub_agent');
    const childText = subAgentPart.parts.find((part: { type: string }) => part.type === 'text');
    expect(childText.settled).toBeUndefined();
  });

  it('is idempotent across multiple tool calls in the same turn (2nd TOOL_CALL_START is a no-op on the buffer)', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'Checking two systems.' }));
    handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));

    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-1', toolCallName: 'jira_search' }));
    spies.onChunk.mockClear();
    handle(frame('TOOL_CALL_START', { runId: 'root', toolCallId: 'call-2', toolCallName: 'confluence_search' }));

    // Buffer was already empty by the 2nd tool call — no extra clearing chunk.
    expect(spies.onChunk).not.toHaveBeenCalled();
    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const textPart = lastParts.find((part: { type: string }) => part.type === 'text');
    expect(textPart).toMatchObject({ settled: true });
  });

  it('emits onParts after REASONING_MESSAGE_END so the closed reasoning block is not stale', () => {
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('REASONING_MESSAGE_START', { runId: 'root' }));
    handle(frame('REASONING_MESSAGE_CONTENT', { runId: 'root', delta: 'thinking...' }));
    spies.onParts.mockClear();

    handle(frame('REASONING_MESSAGE_END', { runId: 'root' }));

    expect(spies.onParts).toHaveBeenCalledTimes(1);
  });

  it('leaves the final answer text unsettled so it renders via AnswerContent, not the timeline', () => {
    // No tool call/reasoning/new turn follows — RUN_FINISHED marks it
    // isFinal instead, which filterRootParts hides from the timeline
    // regardless of `settled`.
    const { callbacks, spies } = makeCallbacks();
    const handle = createAGUIEventHandler(callbacks);

    handle(frame('TEXT_MESSAGE_START', { runId: 'root', messageId: 'm1' }));
    handle(frame('TEXT_MESSAGE_CONTENT', { runId: 'root', delta: 'The final answer.' }));
    handle(frame('TEXT_MESSAGE_END', { runId: 'root', messageId: 'm1' }));
    handle(frame('RUN_FINISHED', { runId: 'root', result: { conversation: {} } }));

    const lastParts = spies.onParts.mock.calls.at(-1)?.[0];
    const textPart = lastParts.find((part: { type: string }) => part.type === 'text');
    expect(textPart).toMatchObject({ content: 'The final answer.', isFinal: true });
    expect(textPart.settled).toBeUndefined();
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

    // The duplicate start must not reset the live answer buffer either —
    // onChunk's `accumulated` should keep reflecting the full turn so far.
    expect(spies.onChunk).toHaveBeenLastCalledWith({
      chunk: ' again',
      accumulated: 'hello again',
      citations: [],
    });
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
