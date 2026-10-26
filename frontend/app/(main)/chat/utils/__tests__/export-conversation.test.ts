import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchConversation = vi.hoisted(() => vi.fn());
const fetchAgentConversation = vi.hoisted(() => vi.fn());
const saveTextFile = vi.hoisted(() => vi.fn());
vi.mock('../../api', () => ({ ChatApi: { fetchConversation: (...a: unknown[]) => fetchConversation(...a) } }));
vi.mock('@/app/(main)/agents/api', () => ({
  AgentsApi: { fetchAgentConversation: (...a: unknown[]) => fetchAgentConversation(...a) },
}));
vi.mock('../save-text-file', () => ({ saveTextFile }));

import { loadHistoricalMessages } from '../../runtime';
import type { ConversationMessage } from '../../types';
import {
  buildConversationMarkdown,
  exportConversation,
  exportFileName,
  fetchAllConversationRows,
} from '../export-conversation';

const LABELS = { you: 'You', assistant: 'Assistant', sources: 'Sources' };

function row(overrides: Partial<ConversationMessage> & Pick<ConversationMessage, '_id' | 'messageType'>): ConversationMessage {
  return {
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: {},
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    feedback: [],
    ...overrides,
  } as ConversationMessage;
}

const question = (id: string, content: string) => row({ _id: id, messageType: 'user_query', content });
const answer = (id: string, content: string, citations: ConversationMessage['citations'] = []) =>
  row({ _id: id, messageType: 'bot_response', content, citations });

function citation(chunkIndex: number, recordName: string, webUrl?: string): ConversationMessage['citations'][number] {
  return {
    citationId: `cite-${chunkIndex}`,
    citationData: {
      _id: `chunk-${chunkIndex}`,
      content: 'excerpt',
      chunkIndex,
      metadata: {
        recordName,
        recordId: `rec-${chunkIndex}`,
        connector: 'DRIVE',
        recordType: 'FILE',
        webUrl,
        mimeType: 'application/pdf',
        extension: 'pdf',
        previewRenderable: true,
        orgId: 'org',
        recordVersion: 1,
      },
      citationType: 'vectorDb|document',
      createdAt: '',
      updatedAt: '',
    },
  } as ConversationMessage['citations'][number];
}

function markdownOf(rows: ConversationMessage[], title = 'Quarterly revenue') {
  return buildConversationMarkdown(title, loadHistoricalMessages(rows).messages, LABELS);
}

function page(messages: ConversationMessage[], hasNextPage: boolean, pageNumber = 1) {
  return { conversation: {}, messages, pagination: { page: pageNumber, limit: 20, hasNextPage } };
}

beforeEach(() => {
  fetchConversation.mockReset();
  fetchAgentConversation.mockReset();
  saveTextFile.mockReset();
});

describe('fetchAllConversationRows', () => {
  it('puts older pages in front, so the chat reads oldest first', async () => {
    fetchConversation
      .mockResolvedValueOnce(page([question('m3', 'third'), answer('m4', 'fourth')], true, 1))
      .mockResolvedValueOnce(page([question('m1', 'first'), answer('m2', 'second')], false, 2));

    const rows = await fetchAllConversationRows('conv-1');

    expect(rows.map((r) => r._id)).toEqual(['m1', 'm2', 'm3', 'm4']);
    expect(fetchConversation).toHaveBeenNthCalledWith(1, 'conv-1', 1);
    expect(fetchConversation).toHaveBeenNthCalledWith(2, 'conv-1', 2);
  });

  it('still reads everything when a new message arrives mid-export and a page overlaps the last', async () => {
    fetchConversation
      .mockResolvedValueOnce(page([question('m3', 'third'), answer('m4', 'fourth')], true, 1))
      .mockResolvedValueOnce(page([answer('m2', 'second'), question('m3', 'third')], true, 2))
      .mockResolvedValueOnce(page([question('m1', 'first')], false, 3));

    const rows = await fetchAllConversationRows('conv-1');

    expect(rows.map((r) => r._id)).toEqual(['m1', 'm2', 'm3', 'm4']);
  });

  it('stops after the last page without asking for another', async () => {
    fetchConversation.mockResolvedValueOnce(page([question('m1', 'only')], false));

    await fetchAllConversationRows('conv-1');

    expect(fetchConversation).toHaveBeenCalledTimes(1);
  });

  it('fails instead of saving a short chat when a page adds nothing but the server says there is more', async () => {
    fetchConversation.mockResolvedValue(page([question('m1', 'same page')], true));

    await expect(fetchAllConversationRows('conv-1')).rejects.toThrow('page 2 added no messages');
    expect(fetchConversation).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['repeats what was read', () => page([question('m1', 'a')], false)],
    ['comes back empty', () => page([], false)],
  ])('fails when the last page %s, since the older messages were never read', async (_name, lastPage) => {
    fetchConversation.mockResolvedValueOnce(page([question('m1', 'a')], true)).mockResolvedValueOnce(lastPage());

    await expect(fetchAllConversationRows('conv-1')).rejects.toThrow('page 2 added no messages');
  });

  it('returns nothing for a chat with no messages', async () => {
    fetchConversation.mockResolvedValueOnce(page([], false));

    expect(await fetchAllConversationRows('conv-1')).toEqual([]);
  });

  it('reads an agent chat from the agent endpoint', async () => {
    fetchAgentConversation
      .mockResolvedValueOnce(page([answer('m2', 'b')], true, 1))
      .mockResolvedValueOnce(page([question('m1', 'a')], false, 2));

    const rows = await fetchAllConversationRows('conv-1', 'agent-9');

    expect(rows.map((r) => r._id)).toEqual(['m1', 'm2']);
    expect(fetchAgentConversation).toHaveBeenNthCalledWith(1, 'agent-9', 'conv-1', { page: 1 });
    expect(fetchAgentConversation).toHaveBeenNthCalledWith(2, 'agent-9', 'conv-1', { page: 2 });
    expect(fetchConversation).not.toHaveBeenCalled();
  });

  it('lets a failed page fail the whole export instead of saving a partial chat', async () => {
    fetchConversation
      .mockResolvedValueOnce(page([answer('m2', 'b')], true))
      .mockRejectedValueOnce(new Error('network'));

    await expect(fetchAllConversationRows('conv-1')).rejects.toThrow('network');
  });
});

describe('buildConversationMarkdown', () => {
  it('writes the title, then each turn under its label, in order', () => {
    const md = markdownOf([question('q1', 'How did revenue change?'), answer('a1', 'It grew **12%**.')]);

    expect(md).toBe('# Quarterly revenue\n\n## You\n\nHow did revenue change?\n\n## Assistant\n\nIt grew **12%**.\n');
  });

  it('leaves the title line out when the chat has none', () => {
    expect(markdownOf([question('q1', 'Hi')], '   ')).toBe('## You\n\nHi\n');
  });

  it('uses the labels it is given, so the file follows the interface language', () => {
    const rows = [question('q1', 'Hallo'), answer('a1', 'Guten Tag')];

    const md = buildConversationMarkdown('Titel', loadHistoricalMessages(rows).messages, {
      you: 'Sie',
      assistant: 'Assistent',
      sources: 'Quellen',
    });

    expect(md).toContain('## Sie\n\nHallo');
    expect(md).toContain('## Assistent\n\nGuten Tag');
  });

  it('lists only the sources an answer cites, in number order, with links', () => {
    const md = markdownOf([
      question('q1', 'Where is it from?'),
      answer('a1', 'First [2] and then [1].', [
        citation(1, 'Annual report', 'https://docs.example.com/report'),
        citation(2, 'Board notes'),
        citation(3, 'Never cited', 'https://docs.example.com/other'),
      ]),
    ]);

    expect(md).toContain(
      '**Sources**\n\n- [1] [Annual report](https://docs.example.com/report)\n- [2] Board notes\n',
    );
    expect(md).not.toContain('Never cited');
  });

  it('does not count an index inside code as a cited source', () => {
    const md = markdownOf([
      question('q1', 'Q'),
      answer('a1', 'Use `items[1]` here.\n\n```js\nvalues[2] = 0;\n```', [
        citation(1, 'Unrelated one', 'https://docs.example.com/one'),
        citation(2, 'Unrelated two'),
      ]),
    ]);

    expect(md).not.toContain('Sources');
    expect(md).toContain('values[2] = 0;');
  });

  it('lists a source the answer wrote as a link', () => {
    const md = markdownOf([
      question('q1', 'Q'),
      answer('a1', 'Costs fell [2](https://docs.example.com/two).', [citation(2, 'Board notes', 'https://docs.example.com/two')]),
    ]);

    expect(md).toContain('**Sources**\n\n- [2] [Board notes](https://docs.example.com/two)\n');
  });

  it('adds no sources section to an answer that cites nothing', () => {
    const md = markdownOf([question('q1', 'Hi'), answer('a1', 'Hello', [citation(1, 'Unused')])]);

    expect(md).not.toContain('Sources');
  });

  it('keeps a source name from breaking out of its link', () => {
    const md = markdownOf([
      question('q1', 'Q'),
      answer('a1', 'See [1].', [citation(1, 'Plan [draft](x) \\ v2')]),
    ]);

    expect(md).toContain('- [1] Plan \\[draft\\](x) \\\\ v2');
  });

  it('links only web addresses, and encodes characters that would end the link early', () => {
    const md = markdownOf([
      question('q1', 'Q'),
      answer('a1', '[1] [2] [3]', [
        citation(1, 'Script', 'javascript:alert(1)'),
        citation(2, 'File', 'file:///etc/passwd'),
        citation(3, 'Spaced', 'https://example.com/a (b)'),
      ]),
    ]);

    expect(md).toContain('- [1] Script\n');
    expect(md).toContain('- [2] File\n');
    expect(md).toContain('- [3] [Spaced](https://example.com/a%20%28b%29)');
  });

  it('removes download and artifact markers the chat shows as cards', () => {
    const md = markdownOf([
      question('q1', 'Make a report'),
      answer(
        'a1',
        'Here it is.\n::download_conversation_task[report.csv](https://our.api/file)\n::artifact[chart.png](https://our.api/c){image/png|d1|r1|image|1}',
      ),
    ]);

    expect(md).toBe('# Quarterly revenue\n\n## You\n\nMake a report\n\n## Assistant\n\nHere it is.\n');
  });

  it('skips an answer that has no text', () => {
    const md = markdownOf([question('q1', 'Hi'), answer('a1', '   ')]);

    expect(md).toBe('# Quarterly revenue\n\n## You\n\nHi\n');
  });

  it('does not show the generated reply to a question card as something the user said', () => {
    const md = markdownOf([
      question('q1', 'Plan a trip'),
      answer('a1', 'Which month?'),
      question('q2', 'User selections: month=June'),
      answer('a2', 'June it is.'),
    ]);

    expect(md).not.toContain('User selections');
  });

  it('keeps the code blocks and tables of an answer as they were written', () => {
    const body = '```python\nprint("hi")\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |';

    expect(markdownOf([question('q1', 'Q'), answer('a1', body)])).toContain(`## Assistant\n\n${body}\n`);
  });

  it('ends with a single new line', () => {
    expect(markdownOf([question('q1', 'Hi')]).endsWith('Hi\n')).toBe(true);
    expect(markdownOf([])).toBe('# Quarterly revenue\n');
  });
});

describe('exportFileName', () => {
  it.each([
    ['Quarterly revenue', 'Quarterly revenue.md'],
    ['  spaced   out  ', 'spaced out.md'],
    ['a/b\\c:d*e?f"g<h>i|j', 'a b c d e f g h i j.md'],
    ['ends with dots...', 'ends with dots.md'],
    ['line\nbreak\tand\u0000nul', 'line break and nul.md'],
    ['', 'chat.md'],
    ['///', 'chat.md'],
    ['日本語のチャット', '日本語のチャット.md'],
  ])('names %j as %j', (title, name) => {
    expect(exportFileName(title)).toBe(name);
  });

  it('cuts a very long title to 80 characters before the extension', () => {
    expect(exportFileName('x'.repeat(200))).toBe(`${'x'.repeat(80)}.md`);
  });

  it('does not split an emoji that sits at the 80 character limit', () => {
    const name = exportFileName(`${'x'.repeat(79)}😀 tail`);

    expect(name).toBe(`${'x'.repeat(79)}😀.md`);
    expect(name).not.toMatch(/[\ud800-\udbff](?![\udc00-\udfff])/);
  });

  it('never leaves a path in the name', () => {
    expect(exportFileName('../../etc/passwd')).not.toMatch(/[\\/]/);
  });
});

describe('exportConversation', () => {
  it('saves the whole chat as a Markdown file named after its title', async () => {
    fetchConversation
      .mockResolvedValueOnce(page([question('m3', 'And Q4?'), answer('m4', 'Up 5%.')], true, 1))
      .mockResolvedValueOnce(page([question('m1', 'Q3?'), answer('m2', 'Up 12%.')], false, 2));

    await exportConversation({ conversationId: 'conv-1', title: 'Revenue', labels: LABELS });

    expect(saveTextFile).toHaveBeenCalledTimes(1);
    const [name, content, mime] = saveTextFile.mock.calls[0];
    expect(name).toBe('Revenue.md');
    expect(mime).toBe('text/markdown');
    expect(content).toBe(
      '# Revenue\n\n## You\n\nQ3?\n\n## Assistant\n\nUp 12%.\n\n## You\n\nAnd Q4?\n\n## Assistant\n\nUp 5%.\n',
    );
  });

  it('saves nothing when the chat cannot be read', async () => {
    fetchConversation.mockRejectedValueOnce(new Error('403'));

    await expect(exportConversation({ conversationId: 'conv-1', title: 'Revenue', labels: LABELS })).rejects.toThrow('403');
    expect(saveTextFile).not.toHaveBeenCalled();
  });
});
