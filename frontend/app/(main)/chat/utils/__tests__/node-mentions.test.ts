import { describe, expect, it } from 'vitest';
import {
  activeMentionQuery,
  caretAfterMention,
  chipDeletionRange,
  insertMention,
  locateMentionNames,
  locateMentions,
  mentionedNodes,
  MENTION_LABEL_MAX,
  mentionChipRemoval,
  mentionLabel,
  MENTION_PAD_CLOSE,
  MENTION_PAD_OPEN,
  mergeMentionFilters,
  retainedMentions,
  stripMentionPads,
  unembedMention,
  withUniqueLabel,
  type MentionNode,
} from '../node-mentions';
import type { AppliedFilterNode } from '../../types';

const budget: MentionNode = {
  id: 'rec-1',
  name: 'Budget 2024',
  nodeType: 'record',
  connector: 'drive',
};

describe('node mentions', () => {
  it('reads the query after @ and ignores a finished mention', () => {
    expect(activeMentionQuery('see @bud', 8)).toEqual({ start: 4, query: 'bud' });
    expect(activeMentionQuery('Budget 2024 is ready', 4)).toBeNull();
    expect(activeMentionQuery('hello @', 7)).toEqual({ start: 6, query: '' });
    expect(activeMentionQuery('email me@work', 12)).toBeNull();
  });

  it('keeps a query open across single spaces, as names have them', () => {
    expect(activeMentionQuery('see @build a pl', 15)).toEqual({ start: 4, query: 'build a pl' });
    expect(activeMentionQuery('see @build ', 11)).toEqual({ start: 4, query: 'build ' });
    expect(activeMentionQuery('meet @ 5pm', 10)).toBeNull();
    expect(activeMentionQuery('see @build  then', 16)).toBeNull();
    expect(activeMentionQuery('see @build\nthen', 15)).toBeNull();
  });

  it('shows a long name cut short and sends it whole', () => {
    const long: MentionNode = { id: 'rec-7', name: 'Proposal for the new year campaign launch', nodeType: 'record', connector: 'confluence' };
    const typed = `${insertMention('see @prop', 4, 9, long)}today`;
    const [span] = locateMentions(typed, [long]);
    const label = typed.slice(span.start, span.end);
    expect(label).toBe('Proposal for the new\u2026');
    expect(long.name.startsWith(label.slice(0, -1))).toBe(true);
    // No word end near the cut: cut mid-word, at full length.
    const oneWord: MentionNode = { ...long, name: 'Supercalifragilisticexpialidocious notes' };
    expect(Array.from(mentionLabel(oneWord))).toHaveLength(MENTION_LABEL_MAX);
    expect(caretAfterMention(4, long)).toBe(typed.indexOf('today'));
    expect(stripMentionPads(typed, [long])).toBe(`see ${long.name} today`);
    expect(locateMentionNames(`see ${long.name} today`, [long])).toEqual([
      { node: long, start: 4, end: 4 + long.name.length },
    ]);
  });

  it('turns one mention back into plain words', () => {
    const long: MentionNode = { id: 'rec-7', name: 'Proposal for the new year campaign launch', nodeType: 'record', connector: 'confluence' };
    const typed = `${insertMention('see @prop', 4, 9, long)}and ${insertMention('@bud', 0, 4, budget)}`;
    const plain = unembedMention(typed, [long, budget], 'rec-7');
    expect(plain.startsWith(`see ${long.name} and `)).toBe(true);
    expect(retainedMentions(plain, [budget]).map((node) => node.id)).toEqual(['rec-1']);
    expect(unembedMention(typed, [long, budget], 'missing')).toBe(typed);
  });

  it('deletes a chip whole, from a key at either edge or inside it', () => {
    const typed = insertMention('see @bud', 4, 8, budget) + 'today';
    const chipStart = 4;
    const chipEnd = typed.indexOf(MENTION_PAD_CLOSE) + MENTION_PAD_CLOSE.length;
    const cut = (range: { start: number; end: number } | null) =>
      range ? typed.slice(0, range.start) + typed.slice(range.end) : typed;
    // Backspace right after it, or from inside its name; the space after it goes too, so no two are left.
    expect(cut(chipDeletionRange(typed, [budget], chipEnd, chipEnd, 'Backspace'))).toBe('see today');
    expect(cut(chipDeletionRange(typed, [budget], chipStart + 6, chipStart + 6, 'Backspace'))).toBe('see today');
    // Not from the space after it, nor before it.
    expect(chipDeletionRange(typed, [budget], chipEnd + 1, chipEnd + 1, 'Backspace')).toBeNull();
    expect(chipDeletionRange(typed, [budget], chipStart, chipStart, 'Backspace')).toBeNull();
    // Delete from just before it.
    expect(cut(chipDeletionRange(typed, [budget], chipStart, chipStart, 'Delete'))).toBe('see today');
    expect(chipDeletionRange(typed, [budget], chipEnd, chipEnd, 'Delete')).toBeNull();
    // A selection over part of it takes all of it; one over all of it is left to the key.
    expect(chipDeletionRange(typed, [budget], 1, chipStart + 6, 'Backspace')).toEqual({ start: 1, end: chipEnd });
    expect(chipDeletionRange(typed, [budget], 0, chipEnd, 'Backspace')).toBeNull();
    expect(retainedMentions(cut(chipDeletionRange(typed, [budget], chipEnd, chipEnd, 'Backspace')), [budget])).toEqual([]);
  });

  it('removes a chip by its span, keeping one space between the words around it', () => {
    const typed = insertMention('@bud', 0, 4, budget) + 'and ' + insertMention('@bud', 0, 4, { ...budget, id: 'rec-2' });
    const [first, second] = locateMentions(typed, [budget, { ...budget, id: 'rec-2' }]);
    const one = mentionChipRemoval(typed, first!);
    expect(typed.slice(0, one.start) + typed.slice(one.end)).toBe(`and ${typed.slice(typed.indexOf('and ') + 4)}`);
    const two = mentionChipRemoval(typed, second!);
    expect((typed.slice(0, two.start) + typed.slice(two.end)).endsWith('and ')).toBe(true);
  });

  it('embeds the chosen name in place of the @ query', () => {
    const next = insertMention('in @bud please', 3, 7, budget);
    expect(next).toBe(`in ${MENTION_PAD_OPEN}Budget 2024${MENTION_PAD_CLOSE}  please`);
    expect(caretAfterMention(3, budget)).toBe(next.indexOf('  please') + 1);
    const start = 3 + MENTION_PAD_OPEN.length;
    expect(locateMentions(next, [budget])).toEqual([{ node: budget, start, end: start + budget.name.length }]);
  });

  it('sends the message without the pads around a mention', () => {
    const typed = `${insertMention('in @bud', 3, 7, budget)}please`;
    expect(stripMentionPads(typed, [budget])).toBe('in Budget 2024 please');
    expect(stripMentionPads('in Budget 2024 please', [budget])).toBe('in Budget 2024 please');
  });

  it('keeps a mention typed in front of one already in the text', () => {
    const earlier: MentionNode = { id: 'rec-2', name: 'Q3 plan', nodeType: 'record', connector: 'jira' };
    const text = insertMention('@q3 Budget 2024 ', 0, 3, earlier);
    expect(locateMentions(text, [budget, earlier]).map((span) => span.node.id)).toEqual(['rec-2', 'rec-1']);
  });

  it('takes the inserted copy of a name, not the same words typed earlier', () => {
    const typed = `${insertMention('compare Budget 2024 with @bud', 25, 29, budget)}today`;
    const [span] = locateMentions(typed, [budget]);
    expect(span.start).toBe(typed.lastIndexOf('Budget 2024'));
    expect(stripMentionPads(typed, [budget])).toBe('compare Budget 2024 with Budget 2024 today');
  });

  it('keeps two nodes of one name apart', () => {
    const other: MentionNode = { ...budget, id: 'rec-9' };
    const first = insertMention('@a', 0, 2, budget);
    const both = insertMention(`${first}and @b`, first.length + 4, first.length + 6, other);
    const spans = locateMentions(both, [budget, other]);
    expect(spans.map((span) => span.node.id)).toEqual(['rec-1', 'rec-9']);
    expect(stripMentionPads(both, [budget, other])).toBe('Budget 2024 and Budget 2024 ');
  });

  it('sends no pads once a mention is edited away', () => {
    const typed = `${insertMention('in @bud', 3, 7, budget)}please`.replace('Budget 2024', 'Budget2025');
    expect(stripMentionPads(typed, [budget])).toBe('in Budget2025 please');
  });

  it('drops a mention once its name is edited, and scopes filters to the node', () => {
    expect(retainedMentions('Budget 202 is ready', [budget])).toEqual([]);
    expect(mergeMentionFilters({ apps: ['app-1'], kb: [] }, [budget])).toEqual({
      apps: ['app-1'],
      kb: [],
      recordGroups: [],
      records: ['rec-1'],
    });
  });

  it('keeps two long names that cut to the same label on their own chips', () => {
    const finance: MentionNode = { id: 'fin', name: 'Quarterly Report 2024 - Finance.pdf', nodeType: 'record', connector: 'drive' };
    const sales: MentionNode = { id: 'sal', name: 'Quarterly Report 2024 - Sales.pdf', nodeType: 'record', connector: 'drive' };
    const first = insertMention('@q', 0, 2, finance);
    const second = withUniqueLabel(sales, [finance]);
    expect(second.label).toBe(sales.name);
    const text = insertMention(`@q and ${first}`, 0, 2, second);
    expect(locateMentions(text, [finance, second]).map((span) => span.node.id)).toEqual(['sal', 'fin']);
    expect(stripMentionPads(text, [finance, second])).toBe(`${sales.name}  and ${finance.name} `);
    expect(withUniqueLabel(sales, [budget])).toBe(sales);
  });
});

describe('where @ opens the menu', () => {
  it('at the start, after a space, and after an opening bracket or quote; never inside an email', () => {
    expect(activeMentionQuery('@bud', 4)).toEqual({ start: 0, query: 'bud' });
    expect(activeMentionQuery('see (@bud', 9)).toEqual({ start: 5, query: 'bud' });
    expect(activeMentionQuery('say "@bud', 9)).toEqual({ start: 5, query: 'bud' });
    expect(activeMentionQuery('mail a@bud', 10)).toBeNull();
  });

  it('takes a query of up to 64 characters', () => {
    const long = 'a'.repeat(64);
    expect(activeMentionQuery(`@${long}`, 65)).toEqual({ start: 0, query: long });
    expect(activeMentionQuery(`@${long}b`, 66)).toBeNull();
  });
});

describe('locateMentionNames: a sent name touching punctuation', () => {
  const node = (id: string, name: string): MentionNode => ({ id, name, nodeType: 'app', connector: 'S3' });
  const S3 = node('s3', 'S3');
  const names = (text: string, mentions: MentionNode[] = [S3]) => locateMentionNames(text, mentions).map((s) => text.slice(s.start, s.end));

  it.each(['in S3?', 'in S3, please', 'in S3.', 'in S3!', '(S3 and more)', 'more (S3)', '"S3" please', 'S3: what changed?', 'in S3...'])(
    'finds it in %s',
    (text) => {
      expect(names(text)).toEqual(['S3']);
    },
  );

  it('finds each one in a sentence of them', () => {
    const mentions = [node('d', 'Team Drive'), node('r', 'report.pdf')];
    expect(names('What is in (Team Drive and report.pdf) too.', mentions)).toEqual(['Team Drive', 'report.pdf']);
  });

  it.each(['S3.txt', "S3's bucket", 'xS3', 'S3x', 'S3-bucket', 'S3_old', 'a/S3/b', 'S3?x'])('leaves %s alone: it is another word', (text) => {
    expect(names(text)).toEqual([]);
  });

  it('the composer keeps its stricter rule: only a space ends a chip it is still tracking', () => {
    expect(locateMentions('in S3?', [S3])).toEqual([]);
  });
});

describe('locateMentionNames: a source the reader cannot open', () => {
  // The server sends it with its kind only: no id and no name. An old question (no `mentioned` flags), or one whose
  // every source is locked for this reader, falls back to its folders and records, the locked ones included.
  const locked = { redacted: true, reason: 'no_access', nodeType: 'folder' } as unknown as AppliedFilterNode;
  const folder = { id: 'fol-1', name: 'Orbit folder', nodeType: 'folder', connector: 'KB' };

  it('is skipped, and the sources that have a name are still found', () => {
    const named = mentionedNodes({ apps: [], kb: [], records: [locked, folder] });
    const spans = locateMentionNames('What is in Orbit folder?', named.map((node) => ({ ...node, extension: null, mimeType: null })));
    expect(spans.map((span) => span.node.id)).toEqual(['fol-1']);
  });

  it('alone: nothing is found, and nothing throws', () => {
    expect(locateMentionNames('What is in it?', mentionedNodes({ apps: [], kb: [], records: [locked] }) as never)).toEqual([]);
  });
});
