import 'reflect-metadata';
import { expect } from 'chai';
import { trimRun } from '../../../src/modules/agent-harness/domain/trim-run';
import { sessionBranch } from '../../../src/modules/agent-harness/domain/repository';
import { downloadName } from '../../../src/modules/agent-harness/transport/session-controller';
import { blockedSiteLine } from '../../../src/modules/agent-harness/domain/site-activity';

describe('trimRun: strips runs at the ends in linear time (CodeQL js/polynomial-redos)', () => {
  it('strips both ends by default, one end when asked', () => {
    expect(trimRun('..a.b..', '.')).to.equal('a.b');
    expect(trimRun('  a  ', ' ', { end: true })).to.equal('  a');
    expect(trimRun('-.-', '-.')).to.equal('');
  });

  it('a long run that does not end the text stays fast', () => {
    const hostile = `${'.'.repeat(200_000)}x`;
    const started = Date.now();
    expect(trimRun(hostile, '.', { end: true })).to.equal(hostile);
    expect(sessionBranch(`${'-'.repeat(200_000)}x`)).to.match(/x$/);
    expect(downloadName(`${' '.repeat(200_000)}x:`)).to.be.a('string');
    // wall-clock-ok: the linear trim takes about a millisecond here; the quadratic regex it replaced takes tens of seconds on 200,000 characters
    expect(Date.now() - started).to.be.below(1_000);
  });

  it('an inherited name is not a blocked reason', () => {
    const view = blockedSiteLine('constructor', 'example.com');
    expect(view?.label).to.equal('Blocked a request to example.com');
  });
});
