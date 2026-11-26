/**
 * The control plane's admin API (`/api/v1/control-plane`, CPD-11) as the
 * existing settings pages use it: policy sections with their history and an
 * agent's grantable items.
 * Every answer is mapped field by field; every failure becomes a `CpError`.
 */
import { apiClient } from '@/lib/api';
import { toAccountsError } from '../ai-accounts/api';
import type { AccountsError } from '../ai-accounts/types';
import { isObject, listOf, num, QUIET, strings, text, type Json } from './pick';

const CP = '/api/v1/control-plane';

export interface CpError extends AccountsError {
  /** The part of the API this deployment does not compose: the page hides the control (no dead control, no retry). */
  off: boolean;
  /** The catalogued code (`CP_WIDENING`, …) when the server named one. */
  code: string | null;
  /** The policy dimension at fault, when the server named one. */
  dimension: string | null;
}

function problemOf(error: unknown): Json | null {
  const original = isObject(error) && isObject(error.originalError) ? error.originalError : null;
  const response = original && isObject(original.response) ? original.response : null;
  const data = response && isObject(response.data) ? response.data : null;
  return data && isObject(data.problem) ? data.problem : null;
}

export function toCpError(error: unknown, write = false): CpError {
  const problem = problemOf(error);
  const remediation = problem && isObject(problem.remediation) ? problem.remediation : null;
  const base = toAccountsError(error, write);
  return {
    ...base,
    // A 422 on a read is a request this page built wrong, never "admins only".
    message: base.message ?? (write ? text(problem?.message) : null),
    off: remediation?.action === 'not_configured',
    code: text(problem?.code),
    dimension: text(problem?.dimension),
  };
}

export const isCpError = (value: unknown): value is CpError =>
  isObject(value) && typeof value.kind === 'string' && typeof value.off === 'boolean';

async function cp<T>(run: () => Promise<T>, write = false): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw toCpError(error, write);
  }
}

const withRevision = (revision: number) => ({ ...QUIET, headers: { 'If-Match': `"${String(revision)}"` } });

// ---- policy sections -------------------------------------------------------

export type PolicySectionName =
  | 'budgets'
  | 'models'
  | 'access'
  // A section an edition adds to the layer (the control plane refuses one this deployment does not offer).
  | (string & {});

export interface PolicySection {
  section: PolicySectionName;
  value: Json | null;
  revision: number;
  /** `budgets.maxTurns` → the most the layers above allow (null: unbounded). */
  ceilings: Record<string, unknown>;
  /** `models.routes` → the configured models an admin may pick from. */
  options: Record<string, string[]>;
  /** Dimension paths the deployment fixed for the org (OWD-12, OWD-18): shown read-only, with a lock. */
  locked: string[];
  /** ADR-UI-03, `access` only: the names of the groups its value holds; a group nobody could name is absent. */
  names?: { groups: Record<string, string> };
}

function toSection(section: PolicySectionName, data: unknown): PolicySection {
  const view = isObject(data) ? data : {};
  const options: Record<string, string[]> = {};
  for (const [path, values] of Object.entries(isObject(view.options) ? view.options : {})) options[path] = strings(values);
  return {
    section,
    value: isObject(view.value) ? view.value : null,
    revision: num(view.revision) ?? 0,
    ceilings: isObject(view.ceilings) ? view.ceilings : {},
    options,
    locked: strings(view.locked),
    ...(isObject(view.names) ? { names: { groups: groupNames(view.names.groups) } } : {}),
  };
}

const groupNames = (value: unknown): Record<string, string> =>
  isObject(value)
    ? Object.fromEntries(
        Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1] !== ''),
      )
    : {};

/**
 * A save or a rollback answers only the section's value and revision; what the layers above say about it (ceilings,
 * options, locks) does not change with the org's own write, so the saved section keeps the read's.
 * The read's group names stay too: a group the write added was named by the search it was picked from.
 */
export function savedOver(read: PolicySection, saved: PolicySection): PolicySection {
  return {
    ...saved,
    ceilings: read.ceilings,
    options: read.options,
    locked: read.locked,
    ...(read.names !== undefined ? { names: read.names } : {}),
  };
}

export interface SectionRevision {
  revision: number;
  value: Json | null;
  at: number | null;
  by: string | null;
}

const toRevision = (entry: unknown): SectionRevision | null => {
  if (!isObject(entry)) return null;
  const revision = num(entry.revision);
  if (revision === null) return null;
  const updated = isObject(entry.updated) ? entry.updated : {};
  return { revision, value: isObject(entry.value) ? entry.value : null, at: num(updated.at), by: text(updated.by) };
};

// ---- grantable -----------------------------------------------------------------

export interface GrantableItem {
  installation: string;
  item: string;
  kind: string;
  granted: boolean;
  state: string;
  reason: string | null;
}

const toGrantable = (entry: unknown): GrantableItem | null => {
  if (!isObject(entry)) return null;
  const installation = text(entry.installation);
  if (installation === null) return null;
  return {
    installation,
    item: text(entry.item) ?? '',
    kind: text(entry.kind) ?? '',
    granted: entry.granted === true,
    state: text(entry.state) ?? 'unavailable',
    reason: text(entry.reason),
  };
};

const seg = encodeURIComponent;

const ORG_POLICY = `${CP}/policy/org/current`;

export const ControlPlaneApi = {
  policy: (section: PolicySectionName) =>
    cp(async () => toSection(section, (await apiClient.get(`${ORG_POLICY}/${section}`, QUIET)).data)),

  savePolicy: (section: PolicySectionName, value: Json | null, revision: number) =>
    cp(async () => toSection(section, (await apiClient.put(`${ORG_POLICY}/${section}`, { value }, withRevision(revision))).data), true),

  history: (section: PolicySectionName, limit = 20) =>
    cp(async () => {
      const { data } = await apiClient.get(`${ORG_POLICY}/${section}/history?limit=${String(limit)}`, QUIET);
      return listOf(isObject(data) ? data.revisions : [], toRevision);
    }),

  rollback: (section: PolicySectionName, toRevision: number, revision: number) =>
    cp(
      async () =>
        toSection(
          section,
          (
            await apiClient.post(
              `${ORG_POLICY}/${section}/rollback`,
              { revision: toRevision },
              withRevision(revision),
            )
          ).data,
        ),
      true,
    ),

  grantable: (agentKey: string) =>
    cp(async () => {
      const { data } = await apiClient.get(`${CP}/agents/${seg(agentKey)}/grantable`, QUIET);
      return listOf(isObject(data) ? data.items : [], toGrantable);
    }),
};
