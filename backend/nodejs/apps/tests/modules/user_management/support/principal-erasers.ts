/**
 * QA15-10 (C-14, P2.4b by behaviour): one recording eraser for each
 * principal store (PRINCIPAL_DATA_STORES) that lists a deletion path among
 * its callers, keyed by the property the path holds it in. Each hook checks,
 * when it is called, that the principal is not marked deleted yet.
 */
import { PRINCIPAL_DATA_STORES } from '../../../../src/modules/agent-harness/domain/session-erasure';

export interface ErasureCall {
  store: string;
  hook: 'eraseUser' | 'eraseOrg';
  args: unknown[];
  principalDeleted: boolean;
}

export interface PrincipalErasers {
  /** By field (`modelAccounts`, `schedules`, `memories`): what the deletion path is constructed with. */
  byField: Record<string, { eraseUser: (...args: unknown[]) => Promise<number>; eraseOrg: (...args: unknown[]) => Promise<number> }>;
  stores: string[];
  calls: ErasureCall[];
}

export function principalErasers(
  caller: string,
  principalDeleted: () => boolean,
  options: { failing?: string } = {},
): PrincipalErasers {
  const listed = PRINCIPAL_DATA_STORES.filter((entry) => entry.callers.includes(caller));
  const calls: ErasureCall[] = [];
  const hook = (store: string, name: ErasureCall['hook']) => async (...args: unknown[]): Promise<number> => {
    calls.push({ store, hook: name, args, principalDeleted: principalDeleted() });
    if (options.failing === store) throw new Error(`${store} unavailable`);
    return 1;
  };
  return {
    byField: Object.fromEntries(
      listed.map((entry) => [entry.field, { eraseUser: hook(entry.store, 'eraseUser'), eraseOrg: hook(entry.store, 'eraseOrg') }]),
    ),
    stores: listed.map((entry) => entry.store),
    calls,
  };
}
