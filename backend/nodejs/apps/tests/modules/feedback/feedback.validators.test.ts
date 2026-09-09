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
    const result = parseBody({
      kind: 'issue',
      description: 'The connector sync fails halfway through a large folder.',
      fileBuffers: [
        {
          buffer: Buffer.alloc(1),
          originalname: 'a.png',
          mimetype: 'image/png',
          size: MAX_FEEDBACK_ATTACHMENTS_TOTAL_BYTES - 1,
        },
        {
          buffer: Buffer.alloc(1),
          originalname: 'b.png',
          mimetype: 'image/png',
          size: 2,
        },
      ],
    });
    expect(result.success).to.equal(false);
  });
});
