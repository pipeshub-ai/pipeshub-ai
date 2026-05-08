/**
 * OAuth 2.0 Dynamic Client Registration (RFC 7591) and Client Registration
 * Management (RFC 7592) — anonymous register + bearer-managed lifecycle.
 *
 * Requires the Node API reachable at `NEXT_PUBLIC_API_BASE_URL` (see `.env.test.example`).
 */
import { test, expect } from '@playwright/test';
import { E2E_API_BASE_URL } from '../fixtures/api-context.fixture';

type ClientRegistrationResponse = {
  client_id: string;
  client_secret?: string;
  client_id_issued_at: number;
  client_secret_expires_at?: number;
  registration_access_token?: string;
  registration_client_uri?: string;
  redirect_uris?: string[];
  grant_types?: string[];
  token_endpoint_auth_method?: string;
};

type ClientRegistrationErrorResponse = {
  error: string;
  error_description?: string;
};

function registerUrl(origin: string): string {
  return `${origin}/api/v1/oauth2/register`;
}

test.describe('Dynamic client registration (RFC 7591 / 7592)', () => {
  test('POST /register creates a public client; GET + DELETE via registration_access_token', async ({
    request,
  }) => {
    const clientName = `e2e-dcr-public-${Date.now()}`;
    const redirectUri = 'http://127.0.0.1:8415/callback';

    const post = await request.post(registerUrl(E2E_API_BASE_URL), {
      headers: { 'Content-Type': 'application/json' },
      data: {
        client_name: clientName,
        redirect_uris: [redirectUri],
        grant_types: ['authorization_code', 'refresh_token'],
        response_types: ['code'],
        token_endpoint_auth_method: 'none',
        application_type: 'native',
      },
    });

    expect(post.status(), await post.text()).toBe(201);
    const created = (await post.json()) as ClientRegistrationResponse;

    expect(created.client_id?.length).toBeGreaterThan(0);
    expect(created.client_id_issued_at).toEqual(expect.any(Number));
    expect(created.registration_access_token?.startsWith('rat_')).toBe(true);
    expect(created.registration_client_uri).toContain(`/register/${created.client_id}`);
    expect(created.client_secret).toBeUndefined();
    expect(created.client_secret_expires_at).toBeUndefined();
    expect(created.redirect_uris).toEqual(expect.arrayContaining([redirectUri]));

    const regToken = created.registration_access_token!;

    const getRes = await request.get(`${registerUrl(E2E_API_BASE_URL)}/${created.client_id}`, {
      headers: { Authorization: `Bearer ${regToken}` },
    });
    expect(getRes.ok(), await getRes.text()).toBeTruthy();
    const meta = (await getRes.json()) as ClientRegistrationResponse;
    expect(meta.client_id).toBe(created.client_id);
    expect(meta.client_name).toBe(clientName);

    const delRes = await request.delete(`${registerUrl(E2E_API_BASE_URL)}/${created.client_id}`, {
      headers: { Authorization: `Bearer ${regToken}` },
    });
    expect(delRes.status()).toBe(204);
  });

  test('POST /register creates a confidential client with client_secret when auth method is not none', async ({
    request,
  }) => {
    const clientName = `e2e-dcr-confidential-${Date.now()}`;
    const redirectUri = 'http://127.0.0.1:8416/callback';

    const post = await request.post(registerUrl(E2E_API_BASE_URL), {
      headers: { 'Content-Type': 'application/json' },
      data: {
        client_name: clientName,
        redirect_uris: [redirectUri],
        grant_types: ['authorization_code', 'refresh_token'],
        response_types: ['code'],
        token_endpoint_auth_method: 'client_secret_basic',
      },
    });

    expect(post.status(), await post.text()).toBe(201);
    const created = (await post.json()) as ClientRegistrationResponse;

    expect(created.client_secret?.length).toBeGreaterThan(0);
    expect(created.client_secret_expires_at).toBeDefined();
    expect(created.registration_access_token?.startsWith('rat_')).toBe(true);

    const delRes = await request.delete(`${registerUrl(E2E_API_BASE_URL)}/${created.client_id}`, {
      headers: { Authorization: `Bearer ${created.registration_access_token!}` },
    });
    expect(delRes.status()).toBe(204);
  });

  test('POST /register rejects client_credentials for DCR clients', async ({ request }) => {
    const post = await request.post(registerUrl(E2E_API_BASE_URL), {
      headers: { 'Content-Type': 'application/json' },
      data: {
        client_name: 'e2e-dcr-bad-grant',
        redirect_uris: ['http://127.0.0.1:9/callback'],
        grant_types: ['client_credentials'],
        token_endpoint_auth_method: 'none',
      },
    });

    expect(post.status()).toBe(400);
    const err = (await post.json()) as ClientRegistrationErrorResponse;
    expect(err.error).toBe('invalid_client_metadata');
    expect(err.error_description).toMatch(/client_credentials/i);
  });

  test('POST /register fails when authorization_code is requested without redirect_uris', async ({
    request,
  }) => {
    const post = await request.post(registerUrl(E2E_API_BASE_URL), {
      headers: { 'Content-Type': 'application/json' },
      data: {
        client_name: 'e2e-dcr-no-redirect',
        grant_types: ['authorization_code'],
        response_types: ['code'],
        token_endpoint_auth_method: 'none',
      },
    });

    expect(post.status()).toBe(400);
    const err = (await post.json()) as ClientRegistrationErrorResponse;
    expect(err.error).toBe('invalid_redirect_uri');
  });

  test('GET /register/:client_id returns 401 without bearer token', async ({ request }) => {
    const fakeId = '00000000-0000-4000-8000-000000000001';
    const res = await request.get(`${registerUrl(E2E_API_BASE_URL)}/${fakeId}`);
    expect(res.status()).toBe(401);
  });
});
