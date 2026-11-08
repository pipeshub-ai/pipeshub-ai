import { describe, it, expect } from 'vitest';
import { authSchemaForInstance, hasSavedAuthValue } from '../auth-schema-for-instance';
import type { AuthSchemaField, ConnectorAuthConfig } from '../../types';

/** OneDrive's sign-in fields as the registry sends them. */
const FIELDS: AuthSchemaField[] = [
  { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true },
  { name: 'clientSecret', displayName: 'Client Secret', fieldType: 'PASSWORD', required: false, isSecret: true, savedOnly: true },
  { name: 'certificate', displayName: 'Certificate', fieldType: 'FILE', required: true, requiredUnlessSaved: 'clientSecret' },
  { name: 'privateKey', displayName: 'Private Key', fieldType: 'FILE', required: true, requiredUnlessSaved: 'clientSecret' },
];

const AUTH: ConnectorAuthConfig = {
  supportedAuthTypes: ['OAUTH_ADMIN_CONSENT'],
  schemas: { OAUTH_ADMIN_CONSENT: { fields: FIELDS } },
  values: {},
};

const fieldsOf = (auth: ConnectorAuthConfig | undefined) => auth?.schemas?.OAUTH_ADMIN_CONSENT.fields ?? [];
const names = (auth: ConnectorAuthConfig | undefined) => fieldsOf(auth).map((f) => f.name);
const required = (auth: ConnectorAuthConfig | undefined, name: string) =>
  fieldsOf(auth).find((f) => f.name === name)?.required;

describe('authSchemaForInstance', () => {
  it('offers a new instance no client secret, and requires the certificate', () => {
    const auth = authSchemaForInstance(AUTH, null);
    expect(names(auth)).toEqual(['clientId', 'certificate', 'privateKey']);
    expect(required(auth, 'certificate')).toBe(true);
    expect(required(auth, 'privateKey')).toBe(true);
  });

  it('keeps the secret of an instance that signs in with one, and makes the certificate optional', () => {
    const auth = authSchemaForInstance(AUTH, { clientId: 'c', clientSecret: '••••••••' });
    expect(names(auth)).toEqual(['clientId', 'clientSecret', 'certificate', 'privateKey']);
    expect(required(auth, 'certificate')).toBe(false);
  });

  it('treats an instance that moved to a certificate as new: no secret, certificate required', () => {
    const auth = authSchemaForInstance(AUTH, { clientId: 'c', clientSecret: '', certificate: '••••••••' });
    expect(names(auth)).not.toContain('clientSecret');
    expect(required(auth, 'certificate')).toBe(true);
  });

  it('leaves fields without the new keys as they are', () => {
    const auth = authSchemaForInstance(AUTH, null);
    expect(fieldsOf(auth)[0]).toBe(FIELDS[0]);
  });

  it('applies to the single-schema format too', () => {
    const auth = authSchemaForInstance({ supportedAuthTypes: [], schema: { fields: FIELDS }, values: {} }, null);
    expect(auth?.schema?.fields.map((f) => f.name)).toEqual(['clientId', 'certificate', 'privateKey']);
  });
});

describe('hasSavedAuthValue', () => {
  it('counts the mask a saved secret comes back as', () => {
    expect(hasSavedAuthValue('••••••••')).toBe(true);
  });

  it('does not count blanks', () => {
    expect([undefined, null, '', '   '].map(hasSavedAuthValue)).toEqual([false, false, false, false]);
  });
});
