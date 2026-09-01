import { expect } from 'chai';
import sinon from 'sinon';

import {
  parseIntSafe,
  parsePositiveIntSafe,
} from '../../../src/libs/utils/env.utils';

describe('parsePositiveIntSafe', () => {
  let warn: sinon.SinonStub;

  beforeEach(() => {
    warn = sinon.stub(console, 'warn');
  });

  afterEach(() => {
    warn.restore();
  });

  it('accepts a whole positive number', () => {
    expect(parsePositiveIntSafe('8', 1)).to.equal(8);
    expect(warn.called).to.be.false;
  });

  it('tolerates surrounding whitespace', () => {
    expect(parsePositiveIntSafe('  8  ', 1)).to.equal(8);
  });

  it('falls back when unset', () => {
    expect(parsePositiveIntSafe(undefined, 4)).to.equal(4);
    expect(warn.called).to.be.false;
  });

  // parseInt accepts a numeric prefix, so each of these would otherwise
  // resolve to a plausible count. Kafka partitions cannot be reduced once
  // grown and lane streams must match the consumer's subscription, so a typo
  // silently becoming a valid number is worse than ignoring it.
  const malformed: Array<[string, string]> = [
    ['4x', 'numeric prefix'],
    ['8lanes', 'trailing word'],
    ['4.9', 'fractional'],
    ['1e3', 'exponent notation'],
    ['0x10', 'hex literal'],
    ['abc', 'non-numeric'],
    ['', 'empty'],
    ['   ', 'blank'],
  ];

  malformed.forEach(([raw, why]) => {
    it(`rejects ${why} (${JSON.stringify(raw)}) and warns`, () => {
      expect(parsePositiveIntSafe(raw, 1)).to.equal(1);
      expect(warn.calledOnce).to.be.true;
    });
  });

  it('rejects values too large to represent exactly', () => {
    // A long run of digits becomes Infinity, which would satisfy `> 0`.
    expect(parsePositiveIntSafe('9'.repeat(400), 8)).to.equal(8);
    expect(parsePositiveIntSafe('9007199254740993', 8)).to.equal(8);
    expect(warn.called).to.be.true;
  });

  it('rejects zero and negatives', () => {
    expect(parsePositiveIntSafe('0', 3)).to.equal(3);
    expect(parsePositiveIntSafe('-2', 3)).to.equal(3);
  });

  it('names the offending variable in the warning', () => {
    parsePositiveIntSafe('4x', 1, 'KAFKA_TOPIC_PARTITIONS');
    expect(warn.firstCall.args[0]).to.contain('KAFKA_TOPIC_PARTITIONS');
  });
});

describe('parseIntSafe', () => {
  let warn: sinon.SinonStub;

  beforeEach(() => {
    warn = sinon.stub(console, 'warn');
  });

  afterEach(() => {
    warn.restore();
  });

  it('accepts integers, including zero and negatives', () => {
    expect(parseIntSafe('500000', 1)).to.equal(500000);
    expect(parseIntSafe('0', 1)).to.equal(0);
    expect(parseIntSafe('-5', 1)).to.equal(-5);
    expect(warn.called).to.be.false;
  });

  it('falls back when unset', () => {
    expect(parseIntSafe(undefined, 500000)).to.equal(500000);
  });

  it('rejects values too large to represent exactly', () => {
    expect(parseIntSafe('9'.repeat(400), 500000)).to.equal(500000);
    expect(parseIntSafe('9007199254740993', 500000)).to.equal(500000);
  });

  // "5e5" previously parsed as 5. For REDIS_STREAMS_MAXLEN that would trim
  // the stream to five entries and discard everything else.
  ['5e5', '500000x', '4.9', 'abc', '', '  '].forEach((raw) => {
    it(`rejects ${JSON.stringify(raw)} rather than truncating it`, () => {
      expect(parseIntSafe(raw, 500000)).to.equal(500000);
      expect(warn.calledOnce).to.be.true;
    });
  });
});
