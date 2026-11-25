/**
 * R46-CARRY (ADR-DOCMIG-01 decision 6): either document store reads a payload name of either form for its org, so a payload the
 * document-store move carried is read by the name it was written under (names inside sealed data and digested event data cannot be
 * renamed), and the session eraser finds it by either name.
 */
import { expect } from 'chai';
import { payloadRowId } from '../../../src/libs/repositories/agent-harness/payload-store/names';
import { pgPayloadId } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import { mongoPayloadId } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const HASH = `sha256:${'b'.repeat(64)}`;

describe('payload names of the document stores (R46-CARRY)', () => {
  it('a name of either store reads as the same row for its org', () => {
    for (const read of [payloadRowId, pgPayloadId, mongoPayloadId]) {
      expect(read(ORG, `mongo:${ORG}:${HASH}`)).to.equal(`${ORG}:${HASH}`);
      expect(read(ORG, `pg:${ORG}:${HASH}`)).to.equal(`${ORG}:${HASH}`);
    }
  });

  it("another org's name, another store's form and an empty hash read as nothing", () => {
    for (const ref of [`pg:${'c'.repeat(24)}:${HASH}`, `memory:${ORG}:${HASH}`, `pg:${ORG}:`, `mongo:${ORG}`, '', `inline:${ORG}:${HASH}`])
      expect(payloadRowId(ORG, ref), ref).to.equal(null);
  });
});
