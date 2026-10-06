import { describe, expect, it } from 'vitest';
import { codeSpanRanges, resolveTypedMentions, typedHandleWords, type ResolverCandidate } from '../typed-mention-resolver';
import type { MentionRef } from '../composer-input.types';

const user = (id: string, label: string): ResolverCandidate => ({ ref: { type: 'user', id }, label });
const team = (id: string, label: string): ResolverCandidate => ({ ref: { type: 'team', id }, label });
const ASSISTANT: MentionRef = { type: 'assistant', id: 'self' };

const BOB = user('u-bob', 'Bob');
const ALICE = user('u-alice', 'Alice Johnson');

const run = (text: string, candidates: ResolverCandidate[] = [], mentions: MentionRef[] = []) =>
  resolveTypedMentions({ text, mentions, candidates });

describe('resolveTypedMentions', () => {
  describe('MN-01 / MN-17: reserved aliases', () => {
    it.each(['@PipesHub hi', '@assistant', '@agent', '@ai', '@AI', 'hey @bot, summarize'])(
      '%s addresses the assistant',
      (text) => {
        const out = run(text);
        expect(out).toEqual({ status: 'ok', text, mentions: [ASSISTANT] });
      },
    );

    it('wins over a participant or agent with the same name', () => {
      const out = run('@assistant hi', [user('u-1', 'Assistant')]);
      expect(out).toMatchObject({ status: 'ok', mentions: [ASSISTANT] });
    });

    it('is added once, after the picked mentions, and is not duplicated by a picked one', () => {
      expect(run('@ai @assistant', [], [{ type: 'user', id: 'x' }])).toMatchObject({
        mentions: [{ type: 'user', id: 'x' }, ASSISTANT],
      });
      expect(run('@ai', [], [ASSISTANT])).toMatchObject({ mentions: [ASSISTANT] });
    });

    it('does not fire inside an address, a longer word, a path or an escaped token', () => {
      for (const text of ['mail me at x@ai.com', 'ping @airline', 'a/@ai', 'x@ai', '<\\@agent:xyz>', 'user@assistant-x']) {
        expect(run(text), text).toMatchObject({ status: 'ok', mentions: [] });
      }
    });

    it('inert aliases do nothing: @everyone, @here, @all stay plain text', () => {
      for (const word of ['everyone', 'here', 'all', 'ALL']) {
        expect(run(`@${word} look`, [user('u-all', 'All Hands')])).toEqual({
          status: 'ok',
          text: `@${word} look`,
          mentions: [],
        });
      }
    });
  });

  describe('MN-02: an exact unique match becomes a token', () => {
    it('a person by name, case-insensitively, keeping the rest of the text', () => {
      const out = run('thanks @bob, see below', [BOB, ALICE]);
      expect(out).toEqual({ status: 'ok', text: 'thanks <@user:u-bob>, see below', mentions: [{ type: 'user', id: 'u-bob' }] });
    });

    it('a full multi-word name, and a first name that only one person has', () => {
      expect(run('@Alice Johnson ok', [BOB, ALICE])).toMatchObject({ text: '<@user:u-alice> ok' });
      expect(run('@Alice ok', [BOB, ALICE])).toMatchObject({ text: '<@user:u-alice> ok' });
    });

    it('a team', () => {
      const sales = team('t-1', 'Sales');
      expect(run('cc @Sales', [sales])).toEqual({ status: 'ok', text: 'cc <@team:t-1>', mentions: [{ type: 'team', id: 't-1' }] });
    });

    it('the exact name wins over a longer one that starts with it', () => {
      const out = run('@Bob', [BOB, user('u-bobby', 'Bobby')]);
      expect(out).toMatchObject({ status: 'ok', text: '<@user:u-bob>' });
    });

    it('several mentions in one message, de-duplicated against picked ones', () => {
      const out = run('@Bob and @Alice, @Bob again', [BOB, ALICE], [{ type: 'user', id: 'u-bob' }]);
      expect(out).toEqual({
        status: 'ok',
        text: '<@user:u-bob> and <@user:u-alice>, <@user:u-bob> again',
        mentions: [{ type: 'user', id: 'u-bob' }, { type: 'user', id: 'u-alice' }],
      });
    });
  });

  describe('MN-03: an ambiguous match asks first', () => {
    const BOBBY = user('u-bobby', 'Bobby');

    it('@Bo matching Bob and Bobby returns the candidates and no text to send', () => {
      const out = run('hello @Bo', [BOB, BOBBY]);
      expect(out).toEqual({ status: 'ambiguous', typed: 'Bo', candidates: [BOB, BOBBY] });
    });

    it('two people with the same name', () => {
      const other = user('u-bob-2', 'Bob');
      expect(run('@Bob', [BOB, other])).toMatchObject({ status: 'ambiguous', candidates: [BOB, other] });
    });

    it('a shared first name', () => {
      const smith = user('u-alice-s', 'Alice Smith');
      expect(run('@Alice', [ALICE, smith])).toMatchObject({ status: 'ambiguous', typed: 'Alice' });
    });

    it('a pick resolves it', () => {
      const out = resolveTypedMentions({
        text: 'hello @Bo',
        mentions: [],
        candidates: [BOB, BOBBY],
        choices: { bo: BOBBY.ref },
      });
      expect(out).toEqual({ status: 'ok', text: 'hello <@user:u-bobby>', mentions: [BOBBY.ref] });
    });
  });

  describe('everything else stays text', () => {
    it('an unknown name, one prefix match, and a lone @', () => {
      expect(run('@Zed hi', [BOB])).toEqual({ status: 'ok', text: '@Zed hi', mentions: [] });
      expect(run('@Bo', [BOB])).toEqual({ status: 'ok', text: '@Bo', mentions: [] });
      expect(run('email me @ noon', [BOB])).toEqual({ status: 'ok', text: 'email me @ noon', mentions: [] });
    });

    it('picked tokens are left alone', () => {
      const text = 'ask <@user:u-bob> to look';
      expect(run(text, [BOB], [BOB.ref])).toEqual({ status: 'ok', text, mentions: [BOB.ref] });
    });

    it('text with no @ is returned as it came', () => {
      expect(run('plain', [BOB])).toEqual({ status: 'ok', text: 'plain', mentions: [] });
    });

    it('stops adding at ten mentions', () => {
      const people = Array.from({ length: 12 }, (_, i) => user(`u${String(i)}`, `Person${String(i)}x`));
      const text = people.map((p) => `@${p.label}`).join(' ');
      const out = run(text, people);
      expect(out.status === 'ok' && out.mentions).toHaveLength(10);
    });
  });
});

describe('typed agent handles (M2)', () => {
  const ref: MentionRef = { type: 'agent', id: 'agent-9' };
  const agents = [{ ref, handle: 'joke-buddy' }];
  const runWith = (text: string, list = agents) => resolveTypedMentions({ text, mentions: [], candidates: [BOB], agents: list });

  it('an exact @handle becomes the agent token, case-insensitively', () => {
    expect(runWith('ask @joke-buddy for one')).toEqual({ status: 'ok', text: 'ask <@agent:agent-9> for one', mentions: [ref] });
    expect(runWith('@Joke-Buddy!')).toMatchObject({ status: 'ok', text: '<@agent:agent-9>!', mentions: [ref] });
  });

  it('the same agent twice is one mention', () => {
    expect(runWith('@joke-buddy and @joke-buddy')).toMatchObject({ mentions: [ref] });
  });

  it('a reserved alias still addresses the assistant', () => {
    expect(runWith('@ai @joke-buddy', [{ ref, handle: 'ai' }])).toMatchObject({ mentions: [ASSISTANT] });
  });

  it.each([
    ['a prefix', '@joke'],
    ['a longer handle', '@joke-buddy-2'],
    ['the display name', '@Joke Buddy'],
    ['an email', 'me@joke-buddy.com'],
    ['a dotted continuation', '@joke-buddy.com'],
    ['inline code', 'run `@joke-buddy` now'],
    ['a double-backtick span', 'run ``a `@joke-buddy` b`` now'],
    ['a fenced block', 'see\n```\n@joke-buddy\n```\ndone'],
    ['an unterminated fence', '```\n@joke-buddy'],
    ['an escaped or tokenised mention', '<@joke-buddy> \\@joke-buddy'],
  ])('never converts %s', (_name, text) => {
    const out = runWith(text);
    expect(out.status === 'ok' && out.mentions.filter((m) => m.type === 'agent')).toEqual([]);
    expect(out.status === 'ok' && out.text).not.toContain('<@agent:');
  });

  it('still converts after a closed code span, and when a lone backtick is plain text', () => {
    expect(runWith('`x` @joke-buddy')).toMatchObject({ mentions: [ref] });
    expect(runWith('it`s @joke-buddy')).toMatchObject({ mentions: [ref] });
  });

  it('property: text that holds no exact @handle outside code comes back unchanged with no agent mention', () => {
    let seed = 7;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };
    const pieces = ['@', 'joke', '-', 'buddy', 'joke-', '-buddy', ' ', '\n', '`', '``', '```', 'x', '.', ',', '@jok', 'e', '2', 'me@', '<', '>', '\\'];
    for (let n = 0; n < 400; n += 1) {
      const text = Array.from({ length: 1 + Math.floor(rnd() * 12) }, () => pieces[Math.floor(rnd() * pieces.length)]).join('');
      const out = runWith(text);
      expect(out.status).toBe('ok');
      if (out.status !== 'ok') continue;
      const holdsHandle = /(^|[\s(\[{"'])@joke-buddy(?![\w@-]|\.\w)/i.test(text);
      if (!holdsHandle) {
        expect(out.text, text).toBe(text);
        expect(out.mentions.filter((m) => m.type === 'agent'), text).toEqual([]);
      }
    }
  });

  it('codeSpanRanges: matched runs, unmatched runs and fences', () => {
    expect(codeSpanRanges('a `b` c')).toEqual([[2, 5]]);
    expect(codeSpanRanges('a ``b ` c`` d')).toEqual([[2, 11]]);
    expect(codeSpanRanges('a ` b')).toEqual([]);
    expect(codeSpanRanges('x\n```\ny')).toEqual([[2, 7]]);
  });

  it('typedHandleWords lists unexplained handle-like words once, outside code', () => {
    const none = () => false;
    expect(typedHandleWords('hi @joke-buddy and @Joke-Buddy, `@code`, @ai, @assistant', none)).toEqual(['joke-buddy']);
    expect(typedHandleWords('@joke-buddy', (w) => w === 'joke-buddy')).toEqual([]);
  });
});
