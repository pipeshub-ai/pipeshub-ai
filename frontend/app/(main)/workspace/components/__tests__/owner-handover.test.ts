import { describe, it, expect } from 'vitest';
import { isOwnerHandoverRefusal } from '../owner-handover';
import policy from '@/lib/i18n/locale-policy.json';

/** CLEANUP-G2: the refusal is recognised by its reason code, never by the server's sentence. */
describe('isOwnerHandoverRefusal', () => {
  const failure = (reasonCode: unknown) => ({ originalError: { response: { data: { error: { reasonCode } } } } });

  it('is true only for the owner-handover reason code', () => {
    expect(isOwnerHandoverRefusal(failure('owner_handover_no_admin'))).toBe(true);
    expect(isOwnerHandoverRefusal(failure('something_else'))).toBe(false);
    expect(isOwnerHandoverRefusal(new Error('owner_handover_no_admin'))).toBe(false);
    expect(isOwnerHandoverRefusal(null)).toBe(false);
  });

  it('every locale explains it for a person and for a service account', async () => {
    for (const name of [policy.source, ...policy.gated]) {
      const messages = (await import(`@/lib/i18n/locales/${name}.json`)).default;
      expect(messages.workspace.users.actions.removeNoAdminForHandover, name).toBeTruthy();
      expect(messages.workspace.serviceAccounts.deleteNoAdminForHandover, name).toBeTruthy();
    }
  });
});
