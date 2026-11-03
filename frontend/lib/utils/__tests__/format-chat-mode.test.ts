import { describe, it, expect } from 'vitest';
import { answerSearchSource, formatChatMode } from '../formatters';

describe('formatChatMode', () => {
  it('reads a snake_case mode in sentence case, like the plus menu', () => {
    expect(formatChatMode('internal_search')).toBe('Internal search');
    expect(formatChatMode('web_search')).toBe('Web search');
  });

  it('is empty without a mode', () => {
    expect(formatChatMode(undefined)).toBe('');
  });
});

describe('answerSearchSource', () => {
  const call = (toolName: string) => ({ type: 'tool_call', toolName });

  it('reads internal search from knowledge tool calls', () => {
    expect(answerSearchSource([call('retrieval.search_internal_knowledge')])).toBe('internal');
    expect(answerSearchSource([call('knowledgehub_list_files')])).toBe('internal');
  });

  it('reads web search from web tools, including inside a sub-agent', () => {
    expect(answerSearchSource([{ type: 'sub_agent', parts: [call('web_search')] }])).toBe('web');
  });

  it('reports both when both were used', () => {
    expect(answerSearchSource([call('retrieval.search'), call('fetch_url')])).toBe('internal_and_web');
  });

  it('ignores search calls that never ran or failed', () => {
    const withStatus = (toolName: string, status: string) => ({ type: 'tool_call', toolName, status });
    expect(answerSearchSource([withStatus('web_search', 'blocked')])).toBeNull();
    expect(answerSearchSource([withStatus('web_search', 'awaiting_approval')])).toBeNull();
    expect(answerSearchSource([withStatus('fetch_url', 'failed')])).toBeNull();
    expect(answerSearchSource([
      withStatus('retrieval.search', 'completed'),
      withStatus('web_search', 'blocked'),
    ])).toBe('internal');
    expect(answerSearchSource([withStatus('web_search', 'running'), withStatus('retrieval.search', 'completed')]))
      .toBe('internal_and_web');
  });

  it('is null when no search tool ran', () => {
    expect(answerSearchSource([call('jira.create_issue'), { type: 'text' }])).toBeNull();
    expect(answerSearchSource(undefined)).toBeNull();
  });
});
