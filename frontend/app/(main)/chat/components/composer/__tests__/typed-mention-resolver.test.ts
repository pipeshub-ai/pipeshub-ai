import { describe, expect, it } from 'vitest';
import { resolveTypedMentions, type ResolverCandidate } from '../typed-mention-resolver';
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

describe('own agents (PH-11.4)', () => {
  const ref: MentionRef = { type: 'agent', id: 'agent-9' };
  const agent: ResolverCandidate[] = [
    { ref, label: 'Offer drafter' },
    { ref, label: 'offer-drafter' },
  ];

  it('a typed @handle becomes the agent token', () => {
    const out = run('ask @offer-drafter to draft', agent);
    expect(out).toEqual({ status: 'ok', text: 'ask <@agent:agent-9> to draft', mentions: [ref] });
  });

  it('the display name resolves too, and name and handle are not ambiguous with each other', () => {
    expect(run('@Offer drafter please', agent)).toMatchObject({ status: 'ok', mentions: [ref] });
  });

  it('without agent candidates the same text stays plain', () => {
    expect(run('ask @offer-drafter', [BOB])).toEqual({ status: 'ok', text: 'ask @offer-drafter', mentions: [] });
  });
});

