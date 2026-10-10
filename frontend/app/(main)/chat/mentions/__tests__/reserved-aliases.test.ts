import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import feAliases from '../reserved-aliases.json';

const NODE_FILE = resolve(
  process.cwd(),
  '../backend/nodejs/apps/src/modules/enterprise_search/services/collaboration/mentions/reserved-aliases.json',
);

describe('reserved aliases (PH10-08)', () => {
  it('the frontend copy deep-equals the Node file', () => {
    expect(feAliases).toEqual(JSON.parse(readFileSync(NODE_FILE, 'utf8')));
  });

  it('is the spec list: pipeshub, assistant, agent, ai, bot act; everyone, here, all are reserved and inert', () => {
    expect([...feAliases.assistant, ...feAliases.inert].sort()).toEqual(
      ['pipeshub', 'assistant', 'agent', 'ai', 'bot', 'everyone', 'here', 'all'].sort(),
    );
  });
});
