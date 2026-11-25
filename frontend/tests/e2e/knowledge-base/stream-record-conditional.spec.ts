/**
 * The record stream route answers a conditional request only to someone who may read the record.
 *
 * A second user in the same organisation, with no access to the record, sends the record's real ETag
 * (and `*`) through the gateway. The answer must be a refusal with no ETag and no file bytes, never a 304.
 */
import { request } from '@playwright/test';
import { test, expect, apiBaseURL } from '../fixtures/api-context.fixture';
import { ensureSmtpConfigured } from '../helpers/smtp.helper';
import { acceptInvite, deleteUserByEmail, signInAs, uniqueMemberEmail } from '../helpers/members.helper';
import { accessTokenFromSession } from '../helpers/session.helper';
import { createTestKb, deleteTestKb, makeKbName, uploadFileByApi } from './kb-upload.helpers';

test.describe('Conditional record stream', () => {
  test('another user gets 403 even with a valid ETag @smoke', async ({ browser, apiContext }) => {
    test.setTimeout(240_000);
    expect(await ensureSmtpConfigured(apiContext), 'SMTP must be configured to invite the second user').toBe(true);

    const kb = await createTestKb(apiContext, makeKbName('conditional'));
    const memberEmail = uniqueMemberEmail('conditional');
    try {
      const recordId = await uploadFileByApi(kb.id, {
        name: `private-${Date.now().toString(36)}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`conditional secret ${'x'.repeat(2000)}`),
      });

      // The owner reads it as the viewer does (a content open): the answer carries the validator.
      const owner = await apiContext.get(`/api/v1/knowledgeBase/stream/record/${recordId}`, { headers: { 'X-Record-Content-Open': '1' } });
      expect(owner.status()).toBe(200);
      const etag = owner.headers()['etag'];
      expect(etag, 'a stored file should be served with an ETag').toBeTruthy();
      const repeat = await apiContext.get(`/api/v1/knowledgeBase/stream/record/${recordId}`, { headers: { 'If-None-Match': etag } });
      expect(repeat.status(), 'the owner revalidates with 304').toBe(304);

      await apiContext.post('/api/v1/users/bulk/invite', { data: { emails: [memberEmail] } });
      const member = await acceptInvite(browser, memberEmail);
      const memberPage = await signInAs(browser, member);
      let token: string;
      try {
        token = await accessTokenFromSession(memberPage);
      } finally {
        await memberPage.context().close();
      }

      const other = await request.newContext({ baseURL: apiBaseURL(), extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
      try {
        for (const validator of [etag, '*']) {
          const res = await other.get(`/api/v1/knowledgeBase/stream/record/${recordId}`, { headers: { 'If-None-Match': validator } });
          expect(res.status(), `If-None-Match ${validator} from a user with no access`).toBe(403);
          expect(res.headers()['etag'], 'a refusal carries no ETag').toBeUndefined();
          expect((await res.body()).includes('conditional secret'), 'a refusal carries no file bytes').toBe(false);
        }
      } finally {
        await other.dispose();
      }
    } finally {
      await deleteUserByEmail(apiContext, memberEmail);
      await deleteTestKb(apiContext, kb.id);
    }
  });
});
