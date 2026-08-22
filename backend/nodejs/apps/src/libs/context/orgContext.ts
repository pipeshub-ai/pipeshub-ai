import { AsyncLocalStorage } from 'node:async_hooks';

export interface OrgContext {
  orgId: string;
}

export const orgContextStorage = new AsyncLocalStorage<OrgContext>();

export function getCurrentOrgId(): string | undefined {
  return orgContextStorage.getStore()?.orgId;
}
