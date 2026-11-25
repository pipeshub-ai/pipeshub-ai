/** `contracts/harness/vectors/policy-resolve.json`, shared with Python's `harness_core.policy` tests (CPD-3). */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { LayerName } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import type { PolicyDimensions } from '../../../../src/modules/agent-harness/contracts/generated/core/policy/policy-dimensions';
import type { OrderSpec } from '../../../../src/modules/agent-harness/domain/policy';

interface ResolveCase {
  id: string;
  layers: Array<{ kind: LayerName; values: unknown }>;
  expect: { config: unknown; digest: string } | { refusal: { code: string; dimension: string; layer: string } };
}
interface OrderEntry {
  id: string;
  order: OrderSpec;
  cases: Array<{ a: unknown; b: unknown; leq: boolean; at?: string; meet?: unknown; meetUndefined?: boolean }>;
}
interface PolicyVectors {
  orders: OrderEntry[];
  cases: ResolveCase[];
  invalidTables: Array<{ id: string; dimensions: PolicyDimensions['dimensions']; names: string }>;
}

export const POLICY_VECTORS = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../../contracts/harness/vectors/policy-resolve.json'), 'utf8'),
) as PolicyVectors;

