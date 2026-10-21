/**
 * Answer components find their slot through `useAnswerSlotId()` / `answerSlotIdNow()`, never by
 * reading the active slot themselves: an answer drawn beside the active chat would otherwise act on
 * (and send into) the active chat. A new component that reads `activeSlotId` fails here.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(__dirname, '..');

/** The list draws the active slot by design; the scope module is where the active-slot fallback lives. */
const ALLOWED = ['message-list.tsx', 'answer-slot-scope.ts'];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) return entry === '__tests__' ? [] : sourceFiles(full);
    return /\.tsx?$/.test(entry) ? [full] : [];
  });
}

const relative = (file: string) => path.relative(ROOT, file).split(path.sep).join('/');

describe('answer slot inventory', () => {
  it('no answer component reads the active slot directly', () => {
    const files = sourceFiles(ROOT).map(relative);
    // The scan sees the answer and its cards, and the exceptions still exist under these names.
    expect(files).toEqual(expect.arrayContaining([...ALLOWED, 'chat-response.tsx', 'tool-approval-card.tsx']));

    const readers = files.filter(
      (file) => !ALLOWED.includes(file) && /\bactiveSlotId\b/.test(readFileSync(path.join(ROOT, file), 'utf8')),
    );

    expect(readers).toEqual([]);
  });
});
