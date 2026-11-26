// The e2e runner's read of a conversation's journal and of the org's audit chain (QA-6, 04 §1 items 2 and 4).
//
//   node /e2e/journal_read.js <orgId> <sessionId>
//
// Runs in the app container with the stack's own Node build (/app/backend/dist), its configuration and SECRET_KEY,
// so the events are unsealed and the chain verified by the product's code (createHarnessJournal, AuditVerifier), not a
// copy of it. Prints one JSON document: every stream of the session in seq order, the audit verification of the org,
// and the types of the org's audit records. Read only.
'use strict';

const path = require('node:path');

const DIST = '/app/backend/dist';
const mongoose = require(path.join(DIST, 'node_modules', 'mongoose'));
const { createHarnessJournal } = require(path.join(DIST, 'modules/agent-harness/composition'));

const PAGE = 1000;

async function readAll(read) {
  const out = [];
  for (let after = 0; ; ) {
    const page = await read(after);
    out.push(...page);
    if (page.length < PAGE) return out;
    after = page[page.length - 1].seq;
  }
}

// A pipe takes a large document in chunks: exiting before it drains would cut it.
const writeOut = (text) => new Promise((resolve) => process.stdout.write(text, resolve));

async function main() {
  const [orgId, sessionId] = process.argv.slice(2);
  if (!orgId || !sessionId) throw new Error('usage: journal_read.js <orgId> <sessionId>');
  await mongoose.connect(process.env.MONGO_URI, { dbName: process.env.MONGO_DB_NAME, serverSelectionTimeoutMS: 10000 });
  try {
    const journal = createHarnessJournal({});
    const streamIds = await mongoose.connection.db
      .collection('sessionEvents')
      .distinct('streamId', { orgId, sessionId });
    const events = [];
    for (const streamId of streamIds.sort()) {
      events.push(...(await readAll((after) => journal.store.readStream(orgId, streamId, after, PAGE))));
    }
    const verification = await journal.verifier.verify(orgId);
    const records = await readAll((after) => journal.audit.read(orgId, after, PAGE));
    await writeOut(
      JSON.stringify({
        events,
        audit: { ok: verification.ok, records: verification.records, problems: verification.problems ?? [] },
        auditTypes: records.map((r) => ({ seq: r.seq, type: r.type ?? r.kind ?? r.event?.type, runId: r.runId ?? r.event?.runId })),
      }),
    );
  } finally {
    await mongoose.disconnect();
  }
}

main().then(
  () => process.exit(0),
  (error) => {
    process.stderr.write(`journal_read: ${error && error.message}\n`);
    process.exit(1);
  },
);
