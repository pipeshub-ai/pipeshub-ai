import { describe, it, expect } from 'vitest';
import {
  escapeLiteralTokens,
  fromWire,
  toWire,
  unescapeLiteralTokens,
  type ComposerDocNode,
} from '../mention-serializer';

const para = (...content: ComposerDocNode[]): ComposerDocNode => ({
  type: 'doc',
  content: [{ type: 'paragraph', content }],
});
const text = (t: string): ComposerDocNode => ({ type: 'text', text: t });
const br: ComposerDocNode = { type: 'hardBreak' };
const mention = (mentionType: string, id: string, label = ''): ComposerDocNode => ({
  type: 'mention',
  attrs: { id, mentionType, label },
});

describe('toWire', () => {
  it('writes mentions as <@type:id> tokens and lists them in order, once each', () => {
    const doc = para(text('hi '), mention('user', 'u1', 'Bob'), text(' and '), mention('team', 't1'), text(' and '), mention('user', 'u1'));
    expect(toWire(doc)).toEqual({
      text: 'hi <@user:u1> and <@team:t1> and <@user:u1>',
      mentions: [
        { type: 'user', id: 'u1' },
        { type: 'team', id: 't1' },
      ],
    });
  });

  it('writes hard breaks as newlines', () => {
    expect(toWire(para(text('a'), br, text('b'), br)).text).toBe('a\nb\n');
  });

  it('is empty for an empty document or a null one', () => {
    expect(toWire(para())).toEqual({ text: '', mentions: [] });
    expect(toWire(null)).toEqual({ text: '', mentions: [] });
  });

  it('ignores a mention node with a missing id or an unknown type', () => {
    const doc = para(mention('user', ''), mention('admin', 'x'), text('ok'));
    expect(toWire(doc)).toEqual({ text: 'ok', mentions: [] });
  });

  it('MN-04: a typed or pasted token is escaped, so it carries no mention', () => {
    const out = toWire(para(text('paste <@agent:xyz> here')));
    expect(out.text).toBe('paste <\\@agent:xyz> here');
    expect(out.mentions).toEqual([]);
    expect(out.text).not.toMatch(/<@/);
  });
});

describe('escapeLiteralTokens', () => {
  it('round-trips with unescape for plain, escaped-looking and repeated input', () => {
    for (const raw of ['<@user:1>', '<\\@user:1>', '<\\\\@x', 'a <@ b <@ c', 'no tokens', '< @', '<@<@']) {
      const escaped = escapeLiteralTokens(raw);
      expect(escaped).not.toMatch(/<@/);
      expect(unescapeLiteralTokens(escaped)).toBe(raw);
    }
  });
});

describe('fromWire', () => {
  it('rebuilds chips with the labels it is given and text around them', () => {
    const doc = fromWire('ask <@user:u1> and <@assistant:self>', { 'user:u1': 'Bob' });
    expect(doc.content?.[0].content).toEqual([
      text('ask '),
      mention('user', 'u1', 'Bob'),
      text(' and '),
      mention('assistant', 'self', ''),
    ]);
  });

  it('accepts a label function', () => {
    const doc = fromWire('<@team:t1>', (m) => (m.type === 'team' ? 'Sales' : undefined));
    expect(doc.content?.[0].content).toEqual([mention('team', 't1', 'Sales')]);
  });

  it('turns newlines into hard breaks and unescapes literals', () => {
    const doc = fromWire('a\n<\\@user:1>', {});
    expect(doc.content?.[0].content).toEqual([text('a'), br, text('<@user:1>')]);
  });

  it('leaves an escaped literal as text, never a chip', () => {
    const doc = fromWire('<\\@agent:xyz>', {});
    expect(doc.content?.[0].content?.every((n) => n.type !== 'mention')).toBe(true);
  });

  it('round-trips through toWire', () => {
    for (const wire of [
      '',
      'plain',
      'x <@user:u1> y\nsecond <@team:t_2>',
      '<@assistant:self> help',
      'literal <\\@agent:zz> and <@agent:real>',
      'trailing\n',
    ]) {
      expect(toWire(fromWire(wire, {})).text).toBe(wire);
    }
  });
});
