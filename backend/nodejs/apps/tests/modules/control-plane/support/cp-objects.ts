/**
 * Valid control-plane objects for the store and resolution contracts, built
 * from the generated types so a fixture cannot drift from the schemas.
 */
import type {
  ApprovalRequest,
  Binding,
  Grant,
  GrantEntry,
  Installation,
} from '../../../../src/modules/control-plane/domain/objects';
import type { StoreTx } from '../../../../src/modules/control-plane/ports/store-tx';
import type { ConfigChange } from '../../../../src/modules/control-plane';

/** Org ids in the shape every store takes (24 lowercase hex; PostgreSQL checks it). */
export const ORG_A = 'a00000000000000000000001';
export const ORG_B = 'b00000000000000000000002';
export const ORG_Z = 'f00000000000000000000009';

/** A Crockford ULID body that sorts by `n`. */
export const ulid = (n: number): string => `01J9ZQ5Y8K3M4N5P6Q7R8S${String(n).padStart(4, '0')}`;
export const instId = (n: number): string => `inst_${ulid(n)}`;
export const bindingId = (n: number): string => `bnd_${ulid(n)}`;
export const approvalId = (n: number): string => `apr_${ulid(n)}`;
export const digest = (fill: string): string => `sha256:${fill.repeat(64).slice(0, 64)}`;

export function anInstallation(overrides: Partial<Installation> = {}): Installation {
  return {
    schemaVersion: 1,
    id: instId(1),
    orgId: ORG_A,
    item: 'mcp:pipeshub/linear@3',
    kind: 'mcp',
    owner: 'org',
    visibility: 'org',
    config: { url: 'https://mcp.example.test/sse' },
    approved: {
      descriptorSetDigest: digest('1'),
      tools: { list_issues: digest('2'), create_issue: digest('3') },
      approvalRef: null,
      at: 1,
      by: 'user:u-admin',
    },
    pending: null,
    status: 'enabled',
    installedVia: null,
    credentialMode: 'none',
    revision: 1,
    updatedAt: 1,
    updatedBy: 'user:u-admin',
    ...overrides,
  };
}

export const anEntry = (installation: string, tools: string[] | null = null, required = false): GrantEntry => ({
  installation,
  tools,
  required,
});

export function aGrant(overrides: Partial<Grant> = {}): Grant {
  return { schemaVersion: 1, orgId: ORG_A, subject: 'agent:a7', entries: [anEntry(instId(1))], revision: 1, ...overrides };
}

export function aBinding(overrides: Partial<Binding> = {}): Binding {
  return {
    schemaVersion: 1,
    id: bindingId(1),
    orgId: ORG_A,
    installation: instId(1),
    owner: 'user:u3',
    mode: 'api_key',
    status: 'active',
    secret: {
      path: `/services/controlPlane/credentials/${ORG_A}/${instId(1)}/user/u3`,
      version: 1,
      versionDigest: digest('4'),
    },
    scopes: [],
    expiresAt: null,
    refreshedAt: null,
    lastError: null,
    revision: 1,
    ...overrides,
  };
}

export function anApproval(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    schemaVersion: 1,
    id: approvalId(1),
    orgId: ORG_A,
    target: { type: 'installation', id: instId(1), revision: 1 },
    change: { type: 'install' },
    changeDigest: digest('5'),
    requestedBy: 'user:u7',
    approvers: [{ principal: 'role:orgAdmin' }],
    state: 'open',
    decisions: [],
    createdAt: 1,
    expiresAt: null,
    ...overrides,
  };
}

/** A transaction whose enlisted writes run at once (the store under test, without audit). */
export const directTx = (orgId: string): StoreTx => ({ orgId, session: undefined, enlist: async (write) => write() });

export const storeChange = (type: string, id: string): ConfigChange => ({
  scope: `org:${ORG_A}`,
  object: { type, id },
  revisionFrom: null,
  revisionTo: null,
  changeDigest: digest('6'),
  changed: [],
});

export async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected a rejection');
}
