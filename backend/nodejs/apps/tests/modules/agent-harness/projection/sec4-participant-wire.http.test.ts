/**
 * SEC4-N2: a participant who is not the run's actor reads the run's SSE
 * stream over HTTP, and the raw bytes they receive carry the mask, never what
 * the actor's run said. The actor's own bytes still carry it (as today, masked
 * only at render). Masking at render is not enough: the bytes are what devtools show.
 */
import { expect } from 'chai';
import { INLINE_CONTENT_REF_PREFIX, inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { GoldenJournal, parseSse, readJournal } from '../../../contracts/support/durable';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { assertNoCanary, VECTOR_EXAMPLES, VECTOR_MASK } from '../support/canary-scan';
import { startStreamHttp, streamRun } from '../support/stream-http';

const ANSWER = 'You get 25 days of paid leave a year.';
const PLANTED = `${ANSWER} Keys seen: ${VECTOR_EXAMPLES.map((e) => e.secret).join(' | ')}`;

function replaced(value: unknown): unknown {
  if (typeof value === 'string') {
    if (!value.startsWith(INLINE_CONTENT_REF_PREFIX)) return value.split(ANSWER).join(PLANTED);
    const text = Buffer.from(value.slice(INLINE_CONTENT_REF_PREFIX.length), 'base64url').toString('utf8');
    return text.includes(ANSWER) ? inlineContentRef(Buffer.from(text.split(ANSWER).join(PLANTED))).ref : value;
  }
  if (Array.isArray(value)) return value.map(replaced);
  if (value === null || typeof value !== 'object') return value;
  const object = Object.fromEntries(Object.entries(value).map(([k, v]) => [k, replaced(v)]));
  // A content ref's hash and size follow its bytes.
  if (typeof object['ref'] === 'string' && object['ref'] !== (value as Record<string, unknown>)['ref']) {
    const bytes = Buffer.from((object['ref'] as string).slice(INLINE_CONTENT_REF_PREFIX.length), 'base64url');
    return { ...object, ...inlineContentRef(bytes) };
  }
  return object;
}

const journal = replaced(readJournal('agent_quick_tool_call')) as GoldenJournal;
const PATH = `/agents/agent-1/conversations/${journal.sessionId}/runs/${journal.runId}/stream`;

describe('SEC4-N2: a participant\'s SSE bytes are shaped on the server', () => {
  let http: Awaited<ReturnType<typeof startStreamHttp>>;
  beforeEach(async () => {
    http = await startStreamHttp({ journal, run: streamRun(journal), records: new InMemoryRecordReadAccess(), citations: [], chat: false });
  });
  afterEach(async () => {
    await http.close();
  });

  it('the participant\'s raw stream carries the answer with every vector example masked', async () => {
    const reply = await http.get(PATH, 'viewer');
    expect(reply.status).to.equal(200);
    const bytes = await reply.text();

    expect(bytes, 'the participant still gets the answer').to.include(ANSWER);
    expect(bytes).to.include(VECTOR_MASK);
    assertNoCanary('participant SSE bytes', bytes, { secrets: VECTOR_EXAMPLES.map((e) => e.secret) });
    expect(parseSse(bytes).map((f) => f.frame.type)).to.not.include('TOOL_CALL_ARGS');
  });

  it('the actor\'s own stream is unchanged', async () => {
    const bytes = await (await http.get(PATH, 'owner')).text();

    for (const { secret } of VECTOR_EXAMPLES) expect(bytes).to.include(JSON.stringify(secret).slice(1, -1));
  });
});
