/**
 * P2.11e AD-1..AD-4: Node reads /services/agentHarness/approvals by the schema
 * and vectors Python reads it with (`tests/unit/harness/test_approvals_parity.py`).
 */
import { expect } from 'chai';
import { foldedApprovals } from './support/approvals-fold';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pipeshubApprovalsDocumentSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/approvals-document';
import {
  approvalLayers,
  normalizeRules,
  readApprovalsDocument,
} from '../../../src/modules/agent-harness/domain/approvals';

interface RuleVector {
  id: string;
  rule: unknown;
  valid: boolean;
  normalized?: unknown;
}

interface DocumentVector {
  id: string;
  description: string;
  document: unknown;
  schemaValid: boolean;
  orgId: string;
  agentKey: string | null;
  status: 'absent' | 'valid' | 'invalid';
  effective: unknown;
}

const VECTORS = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/approvals.json'),
    'utf8',
  ),
) as { rules: RuleVector[]; documents: DocumentVector[] };

describe('P2.11e approvals document parity (AD-1, AD-2)', () => {
  for (const vector of VECTORS.rules) {
    it(`${vector.id}: ${vector.valid ? 'accepts and normalizes' : 'refuses'} the rule`, () => {
      const normalized = normalizeRules(vector.rule);
      if (!vector.valid) {
        expect(normalized).to.equal(null);
        return;
      }
      expect(normalized).to.deep.equal(vector.normalized);
    });
  }
});

describe('P2.11e approvals document parity (AD-3)', () => {
  for (const vector of VECTORS.documents) {
    it(`${vector.id}: ${vector.description}`, () => {
      if (vector.document !== null) {
        expect(
          pipeshubApprovalsDocumentSchema.safeParse(vector.document).success,
        ).to.equal(vector.schemaValid);
      }
      const read = readApprovalsDocument(vector.document);
      expect(read.status).to.equal(vector.status);
      expect(
        foldedApprovals(approvalLayers(read, vector.orgId, vector.agentKey)),
      ).to.deep.equal(vector.effective);
    });
  }
});
