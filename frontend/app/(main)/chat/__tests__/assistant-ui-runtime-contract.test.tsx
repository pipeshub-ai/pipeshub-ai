/**
 * Contract test against the real @assistant-ui/react runtime (no mocks).
 *
 * The chat page, the message list and the input wrapper read thread messages
 * with `useAuiState` and send with `useAui().thread().append(...)`. Every other
 * chat test mocks these hooks, so a package upgrade that renames them would
 * pass unit tests and fail only at build time. This test fails on that.
 */
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import {
  AssistantRuntimeProvider,
  useAui,
  useAuiState,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react';

const onNewSpy = vi.fn();

function Harness() {
  const [messages, setMessages] = useState<ThreadMessageLike[]>([
    { id: 'm1', role: 'user', content: [{ type: 'text', text: 'hello' }] },
  ]);
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: false,
    convertMessage: (m: ThreadMessageLike) => m,
    onNew: async (message) => {
      onNewSpy(message);
      setMessages((prev) => [...prev, { id: `m${prev.length + 1}`, role: 'assistant', content: [{ type: 'text', text: 'reply' }] }]);
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Probe />
    </AssistantRuntimeProvider>
  );
}

function Probe() {
  const messages = useAuiState((s) => s.thread.messages);
  const aui = useAui();
  return (
    <div>
      <span data-testid="count">{messages.length}</span>
      <button
        onClick={() =>
          aui.thread().append({ role: 'user', content: [{ type: 'text', text: 'follow up' }], startRun: true })
        }
      >
        send
      </button>
    </div>
  );
}

describe('assistant-ui runtime contract', () => {
  it('exposes thread messages and routes append() to onNew', async () => {
    render(<Harness />);
    expect(screen.getByTestId('count').textContent).toBe('1');

    await act(async () => {
      screen.getByText('send').click();
    });

    expect(onNewSpy).toHaveBeenCalledTimes(1);
    expect(onNewSpy.mock.calls[0][0].content[0]).toMatchObject({ type: 'text', text: 'follow up' });
    expect(screen.getByTestId('count').textContent).toBe('2');
  });
});
