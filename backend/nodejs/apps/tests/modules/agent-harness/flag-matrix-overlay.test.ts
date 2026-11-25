/**
 * An edition's overlay of a contract document (`contracts/harness/ee/flags.json`):
 * an extension point the C-16 Node half reads through `withOverlay`. It adds
 * entries; it never redefines one. The Python half is test_contract_overlay.py.
 */
import { expect } from 'chai';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  baseFlagMatrix,
  flagMatrix,
  withOverlay,
} from './support/flag-matrix';

const BASE = {
  description: 'base',
  flags: [{ name: 'A_FLAG', default: false }],
  settings: [{ name: 'A_SETTING' }],
  envFlagParse: { true: ['1'] } as Record<string, string[]>,
};

describe('contract overlay (flag matrix)', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'contract-overlay-'));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  const overlay = (document: unknown): string => {
    writeFileSync(join(dir, 'flags.json'), JSON.stringify(document));
    return dir;
  };

  it('no overlay file leaves the document as it is', () => {
    expect(withOverlay(BASE, 'flags.json', dir)).to.equal(BASE);
  });

  it('the matrix without an overlay is the base matrix', () => {
    expect(withOverlay(baseFlagMatrix(), 'flags.json', dir)).to.deep.equal(baseFlagMatrix());
    expect(flagMatrix().flags.length).to.be.at.least(baseFlagMatrix().flags.length);
  });

  it('adds entries after the document\'s own, and keys to a map', () => {
    const merged = withOverlay(
      BASE,
      'flags.json',
      overlay({ description: 'its own', flags: [{ name: 'B_FLAG', default: true }], envFlagParse: { on: [] } }),
    );
    expect(merged.flags.map((f) => f.name)).to.deep.equal(['A_FLAG', 'B_FLAG']);
    expect(merged.envFlagParse).to.deep.equal({ true: ['1'], on: [] });
    expect(merged.description).to.equal('base');
    expect(BASE.flags.map((f) => f.name), 'the base is not changed').to.deep.equal(['A_FLAG']);
  });

  for (const [document, message] of [
    [{ flags: [{ name: 'A_FLAG', default: true }] }, /already has A_FLAG/],
    [{ envFlagParse: { true: ['yes'] } }, /already has true/],
    [{ unknown: [] }, /not a section/],
    [{ flags: { name: 'B_FLAG' } }, /a list or a map/],
  ] as const) {
    it(`refuses ${JSON.stringify(document)}`, () => {
      expect(() => withOverlay(BASE, 'flags.json', overlay(document))).to.throw(message);
    });
  }
});
