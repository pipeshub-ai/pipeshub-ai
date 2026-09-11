/// <reference types="mocha" />
import { expect } from 'chai';
import { createFeedbackSchema } from '../../../src/modules/feedback/validators/feedback.validators';
import { MAX_FEEDBACK_ATTACHMENTS_TOTAL_BYTES } from '../../../src/modules/feedback/schema/feedback.schema';

function parseBody(body: Record<string, unknown>) {
  return createFeedbackSchema.safeParse({ body });
}

describe('feedback/validators', () => {
  it('accepts issue text without files', () => {
    const result = parseBody({
      kind: 'issue',
      description: 'Search results are empty after indexing.',
    });
    expect(result.success).to.equal(true);
  });

  it('rejects a short description', () => {
    const result = parseBody({
      kind: 'feedback',
      description: 'too short',
    });
    expect(result.success).to.equal(false);
  });

  it('rejects attachments that exceed the total size cap', () => {
    const chunk = Math.floor(MAX_FEEDBACK_ATTACHMENTS_TOTAL_BYTES / 3) + 1;
    const result = parseBody({
      kind: 'issue',
      description: 'The connector sync fails halfway through a large folder.',
      fileBuffers: ['a.png', 'b.png', 'c.png'].map((name) => ({
        buffer: Buffer.alloc(chunk),
        originalname: name,
        mimetype: 'image/png',
      })),
    });
    expect(result.success).to.equal(false);
  });
});
