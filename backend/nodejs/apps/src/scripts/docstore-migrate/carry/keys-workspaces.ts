/**
 * Data keys and workspaces (R45-CARRY group 4), carried as one unit: the org's wrapped data keys open every sealed workspace
 * manifest and session key, so neither moves without the other. Keys stay in their wrapped form the whole way (the copy never
 * unwraps one, and the verify reports a differing key by its id and field names, never its value).
 *
 *  - `agentHarnessDataKeys` -> `harness.data_keys` (`PgDataKeyStore`): typed columns, the key id is the document's `_id`;
 *  - `agentWorkspaceHeads` -> `harness.workspace_heads` (by org and workspace) and `agentWorkspaceManifests` ->
 *    `harness.workspace_manifests` (by org, workspace and revision): the document in `doc`, as `PgWorkspaceRepository` writes it;
 *  - `agentWorkspaceKeys` -> `harness.workspace_keys` (by org and session): the document without its keys in `doc`.
 *
 * The workspace documents keep MongoDB's ObjectId `_id`, which the tables do not: they are keyed through the key map.
 */
import { docTableCarrier, type DocTableSpec } from './doc-table';

export const DATA_KEY_AND_WORKSPACE_TABLES: DocTableSpec[] = [
  {
    collection: 'agentHarnessDataKeys',
    table: 'harness.data_keys',
    orgPath: 'orgId',
    key: [{ col: 'kid', path: '_id', kind: 'text' }],
    idCol: 'kid',
    columns: [
      { col: 'version', path: 'version', kind: 'int' },
      { col: 'kek_id', path: 'kekId', kind: 'text' },
      { col: 'wrapped', path: 'wrapped', kind: 'text' },
      { col: 'created_at_ms', path: 'createdAt', kind: 'int' },
      // keys created before seals were counted have none; the store reads an absent count as zero
      { col: 'seals', path: 'seals', kind: 'int', absentAs: 0 },
    ],
  },
  {
    collection: 'agentWorkspaceHeads',
    table: 'harness.workspace_heads',
    orgPath: 'orgId',
    key: [{ col: 'workspace_id', path: 'workspaceId', kind: 'text' }],
    doc: { omit: [] },
  },
  {
    collection: 'agentWorkspaceManifests',
    table: 'harness.workspace_manifests',
    orgPath: 'orgId',
    key: [
      { col: 'workspace_id', path: 'workspaceId', kind: 'text' },
      { col: 'revision', path: 'revision', kind: 'int' },
    ],
    doc: { omit: [] },
  },
  {
    collection: 'agentWorkspaceKeys',
    table: 'harness.workspace_keys',
    orgPath: 'orgId',
    key: [{ col: 'session_id', path: 'sessionId', kind: 'text' }],
    doc: { omit: ['orgId', 'sessionId'] },
  },
];

export const DATA_KEYS_AND_WORKSPACES = docTableCarrier(
  'data-keys-and-workspaces',
  "each org's wrapped data keys, and the conversation workspaces' heads, manifests and per-session keys sealed with them",
  DATA_KEY_AND_WORKSPACE_TABLES,
);
