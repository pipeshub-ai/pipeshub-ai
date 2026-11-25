/**
 * CHILDREF: one bound on a content ref, the contract's.
 *
 * A worker's store inlines payloads up to 16 KiB (`inline:` + base64url),
 * and every journal event takes the contract's `ContentRef`. The commit's
 * `childResult.resultRef` was a hand copy capped at 4,096 characters, so a
 * child's answer over about 3 KB was refused (400 VALIDATION_ERROR) and the
 * Codex child re-ran its turn until it was quarantined.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { childCompletedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/core/events/child-completed';
import { inputQueuedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/core/events/input-queued';
import { CHAT_QUERY_MAX_CHARS } from '../../../src/modules/agent-harness/domain/admission';
import { childFailureResult } from '../../../src/modules/agent-harness/domain/child-runs';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { IMPORT_INLINE_MAX_BYTES } from '../../../src/modules/agent-harness/domain/session-import';
import { inputQueuedEvent } from '../../../src/modules/agent-harness/domain/steering';
import {
  commitRunSchema,
  contentRefSchema,
} from '../../../src/modules/agent-harness/transport/schemas';

const REPO = join(__dirname, '../../../../../..');
const COMMON = JSON.parse(
  readFileSync(join(REPO, 'contracts/harness/core/common.schema.json'), 'utf8'),
) as { $defs: { ContentRef: { properties: { ref: { maxLength: number } } } } };
const REF_MAX = COMMON.$defs.ContentRef.properties.ref.maxLength;
// The worker's store (app/harness/adapters/content_store.py INLINE_MAX_BYTES).
const WORKER_INLINE_MAX_BYTES = 16 * 1024;
const HASH = `sha256:${'0'.repeat(64)}`;

const completion = (ref: string) =>
  commitRunSchema.safeParse({
    params: { runId: 'run-1' },
    body: {
      commandId: 'c-1',
      attemptId: 'a-1',
      fence: 1,
      transition: {
        status: 'completed',
        outcome: 'answer',
        childResult: {
          resultRef: { ref, hash: HASH, size: 1 },
          labels: { integrity: 'internal' },
        },
      },
    },
  });

describe('CHILDREF: a content ref is bounded once, by the contract', () => {
  it('CHILDREF-N1: a child completion takes the largest inline result a worker writes', () => {
    const result = inlineContentRef(Buffer.alloc(WORKER_INLINE_MAX_BYTES, 'x'));
    expect(result.ref.length).to.be.greaterThan(4_096);
    const parsed = completion(result.ref);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).to.equal(true);
  });

  it('CHILDREF-N1: the commit refuses a ref past the contract bound, and takes one at it', () => {
    expect(completion(`inline:${'A'.repeat(REF_MAX - 7)}`).success).to.equal(true);
    const over = completion(`inline:${'A'.repeat(REF_MAX - 6)}`);
    expect(over.success).to.equal(false);
    expect(over.error?.issues[0]?.path.join('.')).to.equal(
      'body.transition.childResult.resultRef.ref',
    );
  });

  it('CHILDREF-N2: the commit’s child result ref is the one child.completed carries', () => {
    expect(contentRefSchema).to.equal(childCompletedEventSchema.shape.data.shape.resultRef);
  });

  it('CHILDREF-N3: every inline ref Node writes fits the bound', () => {
    const steering = inputQueuedEvent(
      1,
      { query: '€'.repeat(CHAT_QUERY_MAX_CHARS), attachments: [], recordIds: [] },
      'user-1',
    );
    const textRef = (steering.data as { textRef: { ref: string } }).textRef;
    expect(textRef.ref.length).to.be.at.most(REF_MAX);
    expect(
      inputQueuedEventSchema.shape.data.shape.textRef.safeParse(textRef).success,
    ).to.equal(true);
    const transcript = inlineContentRef(Buffer.alloc(IMPORT_INLINE_MAX_BYTES, 'x'));
    expect(contentRefSchema.safeParse(transcript).success).to.equal(true);
    expect(contentRefSchema.safeParse(childFailureResult('crash_loop').resultRef).success).to.equal(
      true,
    );
  });
});
