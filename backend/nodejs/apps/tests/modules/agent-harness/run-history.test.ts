/** PERF-B (QA22-F2): a run's history as parts, and the reference that describes them. */
import { expect } from 'chai';
import {
  describeHistory,
  historyBytes,
  historyParts,
  joinHistory,
  parseRunHistoryRef,
} from '../../../src/modules/agent-harness/domain/run-history';
import {
  runFromDocument,
  storedHistoryRef,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';

const turn = (content: string, extra: Record<string, unknown> = {}) => ({
  role: 'bot_response',
  content,
  ...extra,
});

describe('PERFB-9: a history split into parts and joined', () => {
  const cases: Array<[name: string, turns: Array<Record<string, unknown>>, partBytes: number]> = [
    ['no entries', [], 8],
    ['one entry in one part', [turn('hello')], 4096],
    ['an entry larger than a part', [turn('x'.repeat(100))], 16],
    ['exactly one part', [turn('ab')], historyBytes([turn('ab')]).length],
    ['one byte over a part', [turn('ab')], historyBytes([turn('ab')]).length - 1],
    ['multi-byte text cut inside a character', [turn('日本語のテキスト🙂'), turn('ñ')], 5],
    ['nested entries with tool rows', [turn('a', { tools: [{ name: 'read', result: { rows: [1, 2.5, null, true] } }] })], 7],
  ];

  for (const [name, turns, partBytes] of cases) {
    it(`${name}: the parts join to the entries they were cut from`, () => {
      const bytes = historyBytes(turns);
      const parts = historyParts(bytes, partBytes);
      expect(parts.every((part) => part.length <= partBytes)).to.equal(true);
      expect(parts.length).to.equal(Math.ceil(bytes.length / partBytes));
      expect(joinHistory(parts, describeHistory(bytes, turns.length))).to.deep.equal(turns);
    });
  }

  it("an entry's keys come back in the order they were given", () => {
    const turns = [{ zebra: 1, alpha: 2, role: 'user_query' }];
    const bytes = historyBytes(turns);
    const joined = joinHistory(historyParts(bytes, 4), describeHistory(bytes, 1));
    expect(Object.keys(joined![0]!)).to.deep.equal(['zebra', 'alpha', 'role']);
  });

  it('parts that are not what the reference describes join to nothing', () => {
    const turns = [turn('first'), turn('second')];
    const bytes = historyBytes(turns);
    const ref = describeHistory(bytes, turns.length);
    const parts = historyParts(bytes, 10);
    const changed = parts.map((part, index) => (index === 1 ? Buffer.from(part).fill(0x20) : part));

    expect(joinHistory(changed, ref), 'a changed part').to.equal(null);
    expect(joinHistory(parts.slice(1), ref), 'a missing part').to.equal(null);
    expect(joinHistory([...parts].reverse(), ref), 'parts out of order').to.equal(null);
    expect(joinHistory(parts, { ...ref, turns: 3 }), 'another entry count').to.equal(null);
    const notJson = Buffer.from('{"role"');
    expect(joinHistory([notJson], describeHistory(notJson, 1)), 'not JSON').to.equal(null);
    const notEntries = Buffer.from('{"role":"x"}');
    expect(joinHistory([notEntries], describeHistory(notEntries, 1)), 'not a list').to.equal(null);
  });

  it('a part size that cannot cut anything is refused', () => {
    for (const size of [0, -1, 1.5, Number.NaN]) {
      expect(() => historyParts(Buffer.from('abc'), size)).to.throw(RangeError);
    }
  });

  it('a stored reference is read back; anything else is no reference', () => {
    const ref = { parts: ['sealed:run-1/memory:org:sha256:aa'], hash: 'sha256:bb', size: 12, turns: 2 };
    expect(parseRunHistoryRef({ ...ref, extra: true })).to.deep.equal(ref);
    for (const stored of [
      undefined,
      null,
      'sealed:run-1/x',
      [],
      { ...ref, parts: 'sealed:run-1/x' },
      { ...ref, parts: [1] },
      { ...ref, hash: 7 },
      { ...ref, size: '12' },
      { ...ref, turns: null },
    ]) {
      expect(parseRunHistoryRef(stored), JSON.stringify(stored)).to.equal(null);
    }
  });
});

describe('PERFB-2: a stored run names its history inside its input, and the run read from it does not', () => {
  const ref = { parts: ['sealed:run-1/mongo:org:sha256:aa'], hash: 'sha256:bb', size: 12, turns: 2 };
  const stored = (input: unknown) => ({ runId: 'run-1', orgId: 'org-1', harnessMajor: 2, status: 'queued', input });

  it('the reference is read from the stored input and never part of the run', () => {
    const document = stored({ query: 'q', model: { chatMode: 'auto' }, historyRef: ref });

    expect(storedHistoryRef(document)).to.deep.equal(ref);
    const run = runFromDocument(document);
    expect(run.input).to.deep.equal({ query: 'q', model: { chatMode: 'auto' } });
    expect(document.input, 'the document read is left as it was').to.have.property('historyRef');
  });

  it('a document of an older build (history inline), an erased one and one with no input name nothing', () => {
    const inline = stored({ query: 'q', previousConversations: [{ role: 'user_query', content: 'hi' }] });
    expect(storedHistoryRef(inline)).to.equal(null);
    expect(runFromDocument(inline).input?.previousConversations).to.deep.equal([{ role: 'user_query', content: 'hi' }]);
    expect(storedHistoryRef(stored(null))).to.equal(null);
    expect(runFromDocument(stored(null)).input).to.equal(null);
    expect(storedHistoryRef({})).to.equal(null);
    expect(storedHistoryRef(stored({ query: 'q', historyRef: 'not a reference' }))).to.equal(null);
  });
});
