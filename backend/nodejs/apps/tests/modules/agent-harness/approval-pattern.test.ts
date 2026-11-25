/**
 * G-13: the pattern an approval in the Inbox carries (`domain/approval-pattern.ts`, read by the approvals queue).
 * Batch deciding is an edition's since WP-130; the pattern and what keeps a call out of a batch stay in this build.
 */
import { expect } from 'chai';
import { approvalPattern, batchExclusion } from '../../../src/modules/agent-harness/domain/approval-pattern';

describe('G-13: an approval\'s batch pattern', () => {
  it('IB-4: the pattern ignores key order and spacing, never the tool, a value or the destination', () => {
    const base = { toolName: 't', args: '{"a":1,"b":[1,2]}', reason: { rule: 'writes' } };
    expect(approvalPattern({ ...base, args: '{ "b": [1, 2], "a": 1 }' })).to.equal(approvalPattern(base));
    expect(approvalPattern({ ...base, args: '{"a":1,"b":[2,1]}' })).to.not.equal(approvalPattern(base));
    expect(approvalPattern({ ...base, toolName: 'u' })).to.not.equal(approvalPattern(base));
    expect(approvalPattern({ ...base, reason: { rule: 'writes', destination: '#x' } })).to.not.equal(approvalPattern(base));
    // Text that is not JSON is compared byte for byte.
    expect(approvalPattern({ ...base, args: 'not json' })).to.not.equal(approvalPattern({ ...base, args: 'not  json' }));
    expect(batchExclusion({ ...base, reason: { rule: 'engine_writes', sensitivity: 'private' } })).to.equal('read_context');
    expect(batchExclusion({ ...base, reason: { rule: 'engine_writes', sensitivity: 'public' } })).to.equal(null);
    expect(batchExclusion({ ...base, reason: { rule: 'flow_external_write' } })).to.equal('data_flow');
    // A field an edition's rule adds to the reason: that rule decides the call, one at a time.
    expect(batchExclusion({ ...base, reason: { rule: 'writes', sensitivity: 'public', editionPart: { x: 1 } } })).to.equal(
      'edition_rule',
    );
  });
});
