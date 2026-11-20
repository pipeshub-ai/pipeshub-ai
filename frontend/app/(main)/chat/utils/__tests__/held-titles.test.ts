/**
 * A rename is never followed by a reload, so a list read that may predate it keeps the new title: whichever of the
 * rename and the read is answered first.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { holdTitle, resetHeldTitlesForTests, startListRead, withHeldTitles } from '../held-titles';

const rows = (title: string) => [{ id: 'a', title }, { id: 'b', title: 'Other' }];
const titleOfA = (list: Array<{ id: string; title: string }>) => list.find((r) => r.id === 'a')!.title;

beforeEach(() => resetHeldTitlesForTests());

describe('withHeldTitles', () => {
  it('a read answered while the rename is out keeps the new title', () => {
    const read = startListRead();
    holdTitle('a', 'Budget');

    expect(titleOfA(withHeldTitles(rows('Old'), read))).toBe('Budget');
  });

  it('a read that left before the rename was saved, answered after it, keeps the new title', () => {
    const read = startListRead();
    const held = holdTitle('a', 'Budget');
    held.saved();

    expect(titleOfA(withHeldTitles(rows('Old'), read))).toBe('Budget');
  });

  it("a read that left after the save shows the server's title, a later rename by someone else included", () => {
    holdTitle('a', 'Budget').saved();
    const read = startListRead();

    expect(titleOfA(withHeldTitles(rows('Renamed by Bob'), read))).toBe('Renamed by Bob');
  });

  it('a refused rename holds nothing: the old title stands', () => {
    const read = startListRead();
    holdTitle('a', 'Budget').dropped();

    expect(titleOfA(withHeldTitles(rows('Old'), read))).toBe('Old');
  });

  it('a second rename of the same chat is the one held, whatever the first one does', () => {
    const read = startListRead();
    const first = holdTitle('a', 'First');
    holdTitle('a', 'Second');
    first.dropped();

    expect(titleOfA(withHeldTitles(rows('Old'), read))).toBe('Second');
  });

  it('returns the same rows when nothing is held over them', () => {
    const list = rows('Old');
    expect(withHeldTitles(list, startListRead())).toBe(list);
  });
});
