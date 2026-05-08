/**
 * OAuth authorization-server discovery & protected-resource metadata (RFC 8414 / OIDC discovery).
 * Dynamic Client Registration (RFC 7591) clients rely on `registration_endpoint`.
 *
 * Targets the **API** host (`NEXT_PUBLIC_API_BASE_URL`), not the Next.js app (`BASE_URL`).
 */
import { test, expect } from '@playwright/test';
import { E2E_API_BASE_URL } from '../fixtures/api-context.fixture';

test.describe('OIDC / OAuth discovery (DCR prerequisite)', () => {
  test('GET /.well-known/openid-configuration returns issuer and core endpoints', async ({
    request,
  }) => {
    const res = await request.get(`${E2E_API_BASE_URL}/.well-known/openid-configuration`);
    expect(res.ok(), await res.text()).toBeTruthy();

    const body = (await res.json()) as Record<string, unknown>;

    expect(body.issuer).toEqual(expect.any(String));
    expect(body.authorization_endpoint).toEqual(expect.any(String));
    expect(body.token_endpoint).toEqual(expect.any(String));
    expect(body.userinfo_endpoint).toEqual(expect.any(String));
    expect(body.jwks_uri).toEqual(expect.any(String));
    expect(Array.isArray(body.scopes_supported)).toBeTruthy();
    expect(Array.isArray(body.grant_types_supported)).toBeTruthy();
    expect(body.grant_types_supported).toEqual(
      expect.arrayContaining(['authorization_code'])
    );

    const issuer = String(body.issuer).replace(/\/$/, '');
    expect(body.registration_endpoint).toBe(`${issuer}/api/v1/oauth2/register`);
  });

  test('GET /.well-known/oauth-authorization-server matches OIDC discovery (RFC 8414)', async ({
    request,
  }) => {
    const [a, b] = await Promise.all([
      request.get(`${E2E_API_BASE_URL}/.well-known/openid-configuration`),
      request.get(`${E2E_API_BASE_URL}/.well-known/oauth-authorization-server`),
    ]);
    expect(a.ok(), await a.text()).toBeTruthy();
    expect(b.ok(), await b.text()).toBeTruthy();

    const openid = (await a.json()) as Record<string, unknown>;
    const oauthAs = (await b.json()) as Record<string, unknown>;

    expect(oauthAs.issuer).toBe(openid.issuer);
    expect(oauthAs.token_endpoint).toBe(openid.token_endpoint);
  });

  test('GET /.well-known/jwks.json returns a JWKS document', async ({ request }) => {
    const res = await request.get(`${E2E_API_BASE_URL}/.well-known/jwks.json`);
    expect(res.ok(), await res.text()).toBeTruthy();
    const body = (await res.json()) as { keys?: unknown };
    expect(Array.isArray(body.keys)).toBeTruthy();
  });

  test('GET /.well-known/oauth-protected-resource/mcp returns RFC 9728 metadata', async ({
    request,
  }) => {
    const res = await request.get(`${E2E_API_BASE_URL}/.well-known/oauth-protected-resource/mcp`);
    expect(res.ok(), await res.text()).toBeTruthy();
    const body = (await res.json()) as Record<string, unknown>;

    expect(body.resource).toEqual(expect.any(String));
    expect(Array.isArray(body.authorization_servers)).toBeTruthy();
    expect(Array.isArray(body.scopes_supported)).toBeTruthy();
  });
});
