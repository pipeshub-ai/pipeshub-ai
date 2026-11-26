/**
 * SLASH1 D3: the composer's preview renders a /command exactly as the server does — both read
 * contracts/harness/vectors/command-expansion.json.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  ARGUMENT_MAX_UNITS,
  EXPANDED_MAX_UNITS,
  RESERVED_COMMAND_NAMES,
  argumentHint,
  parseTyped,
  renderCommand,
  type CommandArgument,
} from '../render';

interface Case {
  id: string;
  body: string;
  arguments: CommandArgument[];
  args?: string;
  argsRepeat?: { text: string; times: number };
  expected?: string;
  values?: Record<string, string>;
  error?: { code: string; names: string[] };
}

const VECTORS = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../contracts/harness/vectors/command-expansion.json'), 'utf8'),
) as {
  limits: { expandedMaxUnits: number; argumentMaxUnits: number };
  cases: Case[];
  typed: Array<{ text: string; expected: { name: string; args: string } | null }>;
};

describe('renderCommand against the shared vectors', () => {
  it.each(VECTORS.cases.map((c) => [c.id, c] as const))('%s', (_id, c) => {
    const raw = c.argsRepeat ? c.argsRepeat.text.repeat(c.argsRepeat.times) : (c.args ?? '');
    const result = renderCommand(c.body, c.arguments, raw);
    if (c.error) {
      expect(result).toEqual({ ok: false, code: c.error.code, names: c.error.names });
    } else {
      expect(result).toEqual({ ok: true, text: c.expected, values: c.values });
    }
  });

  it.each(VECTORS.typed.map((t) => [JSON.stringify(t.text), t] as const))('parses %s', (_text, t) => {
    expect(parseTyped(t.text)).toEqual(t.expected);
  });

  it('reserves every built-in name, alias and reserved word of the registry, and SLASH1\'s names still', () => {
    const registry = JSON.parse(
      readFileSync(path.resolve(__dirname, '../../../../contracts/harness/ext/pipeshub/commands/builtin-commands.json'), 'utf8'),
    ) as { commands: Array<{ name: string; aliases: string[] }>; reservedWords: Array<{ name: string }> };
    const expected = [
      ...registry.commands.flatMap((c) => [c.name, ...c.aliases]),
      ...registry.reservedWords.map((w) => w.name),
    ].sort();
    expect([...RESERVED_COMMAND_NAMES].sort()).toEqual(expected);
    for (const name of ['compact', 'clear', 'model', 'config', 'effort', 'init', 'help', 'new', 'review', 'login', 'mcp']) {
      expect(RESERVED_COMMAND_NAMES.has(name)).toBe(true);
    }
  });

  it('agrees on the limits', () => {
    expect({ expandedMaxUnits: EXPANDED_MAX_UNITS, argumentMaxUnits: ARGUMENT_MAX_UNITS }).toEqual(VECTORS.limits);
  });
});

describe('argumentHint', () => {
  it('names required arguments, brackets optional ones and lists allowed values', () => {
    expect(
      argumentHint([
        { name: 'incident', required: true },
        { name: 'depth', enum: ['quick', 'full'] },
        { name: 'note' },
      ]),
    ).toBe('incident · depth=quick|full · [note]');
  });
});
