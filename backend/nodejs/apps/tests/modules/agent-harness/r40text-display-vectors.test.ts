/**
 * R40-TEXT (ADR-TEXT-01), Node's half: the display-safe rule (SEC-B-04) and the spreadsheet-cell rule (SEC-B-06)
 * hold here as in Python and the web client. The shared vectors are `contracts/harness/vectors/display-safe.json`
 * and `contracts/harness/vectors/spreadsheet-cells.json`; a table in code that drifts from them fails.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect } from 'chai';
import * as ds from '../../../src/modules/agent-harness/domain/display-safe';
import * as sc from '../../../src/libs/utils/spreadsheet-cell';
import { approvalReason } from '../../../src/modules/agent-harness/projection/timeline';

const REPO = path.resolve(__dirname, '../../../../../..');
const read = (name: string): unknown =>
  JSON.parse(readFileSync(path.join(REPO, 'contracts/harness/vectors', name), 'utf8'));

interface DisplayVector {
  marker: { open: string; close: string };
  invisible: Array<[string, string]>;
  proseKeeps: { controls: string[]; joiners: string[]; variationSelectors: string[] };
  scripts: Array<{ name: string; ranges: Array<[string, string]> }>;
  allowedMixes: string[][];
  lookalikes: Record<string, string>;
  latinPassesForAscii: string;
  hiddenMarks: string[];
  text: Array<{ name: string; mode: ds.DisplayMode; in: string; out: string }>;
  hosts: Array<{ name: string; in: string; out: string }>;
}
interface CellVector {
  prefix: string;
  triggers: string[];
  startsAlways: string[];
  signs: string[];
  leading: string[];
  cases: Array<{ name: string; in: string; out: string }>;
}

const hex = (code: string): number => parseInt(code, 16);
const ranges = (pairs: Array<[string, string]>): Array<[number, number]> => pairs.map(([a, b]) => [hex(a), hex(b)]);
const chars = (codes: string[]): string => codes.map((c) => String.fromCodePoint(hex(c))).join('');
const sorted = (text: string): string => Array.from(text).sort().join('');

describe('R40-TEXT: the display-safe vector (Node)', () => {
  const v = read('display-safe.json') as DisplayVector;

  it('carries the vector\'s tables', () => {
    expect([ds.MARKER_OPEN, ds.MARKER_CLOSE]).to.deep.equal([v.marker.open, v.marker.close]);
    expect(ds.INVISIBLE_RANGES).to.deep.equal(ranges(v.invisible));
    expect(ds.PROSE_CONTROLS).to.deep.equal(v.proseKeeps.controls.map(hex));
    expect(ds.PROSE_JOINERS).to.deep.equal(v.proseKeeps.joiners.map(hex));
    expect(ds.PROSE_VARIATION_SELECTORS).to.deep.equal(v.proseKeeps.variationSelectors.map(hex));
    expect(ds.SCRIPTS).to.deep.equal(v.scripts.map((s) => ({ name: s.name, ranges: ranges(s.ranges) })));
    expect(ds.ALLOWED_MIXES).to.deep.equal(v.allowedMixes);
    expect(ds.LOOKALIKES).to.deep.equal(v.lookalikes);
    expect(ds.LATIN_PASSES_FOR_ASCII).to.equal(v.latinPassesForAscii);
    expect(ds.HIDDEN_MARKS).to.deep.equal(v.hiddenMarks.map(hex));
  });

  v.text.forEach((c) => {
    it(`text: ${c.name}`, () => {
      const shown = ds.displaySafe(c.in, c.mode);
      expect(shown).to.equal(c.out);
      expect(ds.displaySafe(shown, c.mode)).to.equal(shown);
    });
  });

  v.hosts.forEach((c) => {
    it(`host: ${c.name}`, () => {
      const shown = ds.safeHost(c.in);
      expect(shown).to.equal(c.out);
      expect(ds.safeHost(shown)).to.equal(shown);
    });
  });

  it('encodes and decodes punycode as the platform does', () => {
    const url = require('node:url') as typeof import('node:url');
    ['p\u0456peshub', 'm\u00fcnchen', '\u0440\u0430\u0443\u0440\u0430\u04cf', '\u65e5\u672c\u8a9e', '\u2603', 'g\u03bfogle', 'p\u1ea1ypal'].forEach((label) => {
      const ascii = `xn--${ds.punycodeEncode(label)}`;
      expect(url.domainToASCII(`${label}.com`)).to.equal(`${ascii}.com`);
      expect(ds.punycodeDecode(ascii.slice(4))).to.equal(label);
    });
    expect(ds.punycodeDecode('zz')).to.equal(null);
  });

  it('finds the punycode hosts a card shows, with what they decode to', () => {
    expect(ds.punycodeHosts('drop@xn--ttacker-1fg.example, a@x.example')).to.deep.equal([
      { ascii: 'xn--ttacker-1fg.example', unicode: 'аttacker.example' },
    ]);
    expect(ds.punycodeHosts('plain text')).to.deep.equal([]);
  });

  it('never cuts a marker in half', () => {
    const text = `${'a'.repeat(10)}⟨U+202E⟩${'b'.repeat(10)}`;
    for (let limit = 5; limit < text.length + 2; limit += 1) {
      const clipped = ds.clipDisplay(text, limit);
      expect(Array.from(clipped).length).to.be.at.most(limit);
      expect(clipped.split('⟨').length).to.equal(clipped.split('⟩').length);
    }
  });

  it('keeps a third-party title from naming another owner', () => {
    expect(ds.toolDisplayName('Third party', 'Slack · Send message')).to.equal('Third party · Slack - Send message');
    expect(ds.toolDisplayName('Third party', 'Read\ncalendar')).to.equal('Third party · Read calendar');
  });

});

describe('R40-TEXT: the spreadsheet-cell vector (Node)', () => {
  const v = read('spreadsheet-cells.json') as CellVector;

  it('carries the vector\'s sets', () => {
    expect(sc.SPREADSHEET_PREFIX).to.equal(v.prefix);
    expect(sorted(sc.TRIGGERS)).to.equal(sorted(chars(v.triggers)));
    expect(sorted(sc.STARTS_ALWAYS)).to.equal(sorted(chars(v.startsAlways)));
    expect(sorted(sc.SIGNS)).to.equal(sorted(chars(v.signs)));
    expect(sorted(sc.LEADING)).to.equal(sorted(chars(v.leading)));
  });

  v.cases.forEach((c) => {
    it(`cell: ${c.name}`, () => {
      expect(sc.spreadsheetCell(c.in)).to.equal(c.out);
      expect(sc.spreadsheetCell(sc.spreadsheetCell(c.in))).to.equal(sc.spreadsheetCell(c.in));
    });
  });

});

describe('R40-TEXT: Node serves the card display-safe text (SEC-B-04)', () => {
  it("a journal's destination and source titles reach the card with markers, whoever wrote them", () => {
    const reason = approvalReason({
      rule: 'flow_external_write',
      sensitivity: 'private',
      sources: [{ title: 'Payroll \u202efdp.exe', recordId: 'rec-1' }],
      destination: { label: 'drop@\u0430ttacker.example, \u202emoc.live@bob', key: 'k' },
      conversationGrant: true,
    });
    expect(reason?.destination).to.equal('drop@xn--ttacker-1fg.example, \u27e8U+202E\u27e9moc.live@bob');
    expect(reason?.sources[0]?.title).to.equal('Payroll \u27e8U+202E\u27e9fdp.exe');
  });

  it('plain text is served as it is', () => {
    const reason = approvalReason({ rule: 'flow_external_write', sources: [{ title: 'Q3 plan' }], destination: { label: '#sales', key: 'k' } });
    expect(reason?.destination).to.equal('#sales');
    expect(reason?.sources[0]?.title).to.equal('Q3 plan');
  });
});
