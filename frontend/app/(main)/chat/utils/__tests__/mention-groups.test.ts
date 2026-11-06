import { describe, expect, it } from 'vitest';
import {
  MENTION_MENU_MAX_ROWS,
  filterMentionItems,
  layoutMentionGroups,
  mentionMatches,
  rankMentionItems,
  sourcesOverCap,
  type MentionGroupView,
  type MentionMenuItem,
} from '../mention-groups';

const item = (label: string, extra: Partial<MentionMenuItem> = {}): MentionMenuItem => ({ key: label, label, value: label, ...extra });
const group = (key: string, count: number, limit: number, extra: Partial<MentionGroupView> = {}): MentionGroupView => ({
  key,
  items: Array.from({ length: count }, (_, i) => item(`${key}-${i}`)),
  limit,
  status: 'ready',
  ...extra,
});
const shown = (sections: ReturnType<typeof layoutMentionGroups>) =>
  sections.map((s) => [s.group.key, s.rows.filter((r) => r.type === 'item').length, s.rows.find((r) => r.type === 'more')]);

describe('mentionMatches', () => {
  it('matches the start of any word, or of the handle', () => {
    expect(mentionMatches('Priya Nair', 'na')).toBe(true);
    expect(mentionMatches('Priya Nair', 'ir')).toBe(false);
    expect(mentionMatches('Sales Bot', 'sb', 'sbot')).toBe(true);
    expect(mentionMatches('Q3_budget.xlsx', 'bud')).toBe(true);
  });

  it('reads several words as a name typed in order', () => {
    expect(mentionMatches('John Michael Smith', 'jo sm')).toBe(true);
    expect(mentionMatches('John Michael Smith', 'sm jo')).toBe(false);
  });
});

describe('rankMentionItems', () => {
  it('puts recent rows first, latest first, then keeps the given order with nothing typed', () => {
    const rows = [item('Ana'), item('Ben', { recentAt: 10 }), item('Cy'), item('Dee', { recentAt: 20 })];
    expect(rankMentionItems(rows, '').map((r) => r.label)).toEqual(['Dee', 'Ben', 'Ana', 'Cy']);
  });

  it('for a typed name: recent, then first name or handle, then any word, then alphabetical', () => {
    const rows = [item('Zed Park'), item('Al Parker'), item('Paula Diaz'), item('Pat Ng'), item('Bo Pak', { recentAt: 5 }), item('Xi', { handle: 'pamela' })];
    expect(rankMentionItems(rows, 'pa').map((r) => r.label)).toEqual(['Bo Pak', 'Pat Ng', 'Paula Diaz', 'Xi', 'Al Parker', 'Zed Park']);
  });

  it('filters to what matches, keeping pinned rows', () => {
    const rows = [item('Assistant', { pinned: true }), item('Ana'), item('Ben')];
    expect(filterMentionItems(rows, 'an').map((r) => r.label)).toEqual(['Assistant', 'Ana']);
  });
});

describe('layoutMentionGroups', () => {
  it('shows each group up to its limit, with "N more" for the rest, and leaves empty groups out', () => {
    const sections = layoutMentionGroups([group('people', 7, 5), group('agents', 0, 3), group('sources', 2, 6)]);
    expect(shown(sections)).toEqual([
      ['people', 5, { type: 'more', groupKey: 'people', count: 2 }],
      ['sources', 2, undefined],
    ]);
  });

  it(`keeps a bare menu to ${MENTION_MENU_MAX_ROWS} rows: the group showing most gives one up first, the later on a tie`, () => {
    const sections = layoutMentionGroups([
      group('inChat', 10, 8, { items: [item('Assistant', { pinned: true }), item('Private', { pinned: true }), item('everyone', { pinned: true }), ...Array.from({ length: 7 }, (_, i) => item(`m${i}`))] }),
      group('notInChat', 5, 3),
      group('agents', 5, 3),
      group('apps', 5, 3),
      group('collections', 5, 3),
    ]);
    const counts = shown(sections).map(([key, n]) => [key, n]);
    expect(counts).toEqual([['inChat', 6], ['notInChat', 2], ['agents', 2], ['apps', 2], ['collections', 2]]);
    expect(counts.reduce((sum, [, n]) => sum + (n as number), 0)).toBe(MENTION_MENU_MAX_ROWS);
  });

  it('a group still loading or failed keeps its place; one with a way on shows that row', () => {
    const open = () => {};
    const sections = layoutMentionGroups([
      group('people', 0, 5, { status: 'loading' }),
      group('agents', 0, 3, { status: 'error' }),
      { key: 'browse', items: [], limit: 0, status: 'ready', browse: { label: 'Browse all sources', open } },
    ]);
    expect(sections.map((s) => s.group.key)).toEqual(['people', 'agents', 'browse']);
    expect(sections[2].rows).toEqual([{ type: 'more', groupKey: 'browse', count: null, browse: { label: 'Browse all sources', open } }]);
  });

  it('a searched group the server has more of says Show more (no count)', () => {
    const [section] = layoutMentionGroups([group('sources', 20, 5, { hasMore: true })]);
    expect(section.rows.at(-1)).toEqual({ type: 'more', groupKey: 'sources', count: null });
  });

  it('opened, a group shows whole', () => {
    const sections = layoutMentionGroups([group('people', 9, 5), group('agents', 3, 3)], { expanded: 'people' });
    expect(shown(sections)).toEqual([['people', 9, undefined]]);
  });
});

describe('sourcesOverCap', () => {
  it('counts the sources past ten', () => {
    expect(sourcesOverCap(10)).toBe(0);
    expect(sourcesOverCap(12)).toBe(2);
  });
});
