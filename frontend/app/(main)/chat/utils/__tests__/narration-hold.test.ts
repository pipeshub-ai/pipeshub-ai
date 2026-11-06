import { describe, it, expect } from 'vitest';
import { mayBeNarration } from '../narration-hold';

describe('mayBeNarration', () => {
  it('holds what an agent says before a tool call: one or two short sentences', () => {
    for (const text of [
      'I',
      'I’ll ask which team you’re on first, then pause here for your answer.',
      'I’ll search your collections for the code freeze rules and pull the clearest sources.',
      'I found the release guide; I’m checking its full procedure and whether another collection adds rules.',
      'You’re on Platform. I’ll check the release guidance so the checklist reflects your team.',
      // Version numbers are not sentence ends.
      'I’ll compare releases 4.1, 4.2 and 5.0 in the handbook and note their freeze dates.',
    ]) {
      expect(mayBeNarration(text), text).toBe(true);
    }
  });

  it('lets text through as the answer once it starts a third sentence', () => {
    expect(mayBeNarration('Code review catches bugs early. It also spreads knowledge.')).toBe(true);
    expect(mayBeNarration('Code review catches bugs early. It also spreads knowledge. And')).toBe(false);
  });

  it('lets it through once it breaks a line or opens with a block', () => {
    expect(mayBeNarration('### Release process\n\nThe')).toBe(false);
    expect(mayBeNarration('Release process\nThe')).toBe(false);
    for (const text of ['## Lists', '- Code freeze begins', '1. Confirm the freeze', '> Note', '| Team |', '```py']) {
      expect(mayBeNarration(text), text).toBe(false);
    }
  });

  it('lets it through past 280 characters, not counting link targets', () => {
    const long = `The release process ${'moves through freeze, branch and staging '.repeat(7)}`;
    expect(long.length).toBeGreaterThan(280);
    expect(mayBeNarration(long)).toBe(false);
    const cited = `I found the release guide [1](https://example.com/record/${'r'.repeat(300)}) and I’m reading it now.`;
    expect(mayBeNarration(cited)).toBe(true);
  });

  it('holds a two-sentence narration whose second sentence has a clause after a semicolon', () => {
    // Said before a tool call in a recorded run: read as three sentences, it showed as the answer and then moved.
    expect(
      mayBeNarration(
        'I found the release-process document in the Engineering Handbook. I’m reading it in full so the summary covers ' +
          'all stated steps; then I’ll ask you to choose the length before I present it.',
      ),
    ).toBe(true);
  });

  it('does not count colons or semicolons as sentence ends', () => {
    expect(mayBeNarration('I found the guide: it covers the freeze. Next I’ll check the captain’s steps')).toBe(true);
    expect(mayBeNarration('I found two guides; one covers the freeze. Next I’ll read the other')).toBe(true);
    // An answer that opens with a label is let through at its third real sentence, like any other.
    expect(mayBeNarration('Short answer: yes. The freeze starts ten days early.')).toBe(true);
    expect(mayBeNarration('Short answer: yes. The freeze starts ten days early. QA')).toBe(false);
  });

  it('counts Chinese and Japanese sentence ends, with no space after them', () => {
    const zh = ['发布流程从代码冻结开始。', '冻结在每次发布前十天生效。', '发布负责人负责切出发布分支。'];
    const ja = ['リリースはコードフリーズから始まります。', 'フリーズは各リリースの十日前に始まります！', 'リリース担当者がブランチを切ります。'];
    for (const [a, b, c] of [zh, ja]) {
      expect(mayBeNarration(a + b), a + b).toBe(true);
      expect(mayBeNarration(a + b + c.slice(0, 1)), a + b + c.slice(0, 1)).toBe(false);
    }
    // A full-width closing quote or bracket after the end belongs to it.
    expect(mayBeNarration('负责人说「先冻结代码。」团队随后切出分支？然后')).toBe(false);
  });

  it('holds a Chinese narration of one or two sentences', () => {
    for (const text of ['我会先在你的文档中查找发布流程，然后问你属于哪个团队。', '我找到了发布流程文档。我会完整阅读后再总结要点。']) {
      expect(mayBeNarration(text), text).toBe(true);
    }
  });

  it('has nothing to hold in empty text', () => {
    expect(mayBeNarration('')).toBe(false);
    expect(mayBeNarration('  \n ')).toBe(false);
  });
});
