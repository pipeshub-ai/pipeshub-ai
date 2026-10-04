import { describe, expect, it } from 'vitest';
import { createInstance } from 'i18next';
import { collectSyncCustomFieldErrors, validateSyncCustomField } from '../sync-custom-fields-validation';
import { collectAuthFieldErrors } from '../../components/authenticate-tab/auth-step-validation';
import { CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME } from '../../constants';
import type { SyncCustomField, AuthSchemaField } from '../../types';
import de from '@/lib/i18n/locales/de-DE.json';

const field = { name: 'website', displayName: 'Website', fieldType: 'URL', required: true } satisfies SyncCustomField;

describe('connector field validation', () => {
  it('preserves required, tag, URL and length checks', () => {
    expect(validateSyncCustomField(field, '')).toBe('fieldRequired');
    expect(validateSyncCustomField({ ...field, required: false }, '')).toBeNull();
    expect(validateSyncCustomField({ ...field, fieldType: 'TAGS' }, [' '])).toBe('fieldRequired');
    expect(validateSyncCustomField(field, 'example.com')).toBeNull();
    expect(validateSyncCustomField(field, 'ftp://example.com')).toBe('urlProtocol');
    expect(validateSyncCustomField(field, ':::')).toBe('url');
    expect(validateSyncCustomField({ ...field, validation: { minLength: 10 } }, 'abc')).toBe('minLength');
    expect(validateSyncCustomField({ ...field, validation: { maxLength: 2 } }, 'abc')).toBe('maxLength');
  });

  it('keeps the service-account JSON maximum-length exemption', () => {
    expect(validateSyncCustomField({
      ...field, name: CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME, fieldType: 'TEXT', validation: { maxLength: 2 },
    }, '{"key":"value"}')).toBeNull();
  });

  it('lets the UI translate sync and auth errors while preserving schema display names', async () => {
    const i18n = createInstance();
    await i18n.init({ lng: 'de-DE', resources: { 'de-DE': { translation: de } } });
    const messageFor = (schemaField: { displayName: string }, code: string) =>
      i18n.t(`workspace.actions.validation.${code}`, { field: schemaField.displayName });
    expect(collectSyncCustomFieldErrors([field], { website: 'ftp://example.com' }, messageFor))
      .toEqual({ website: 'Website muss http oder https verwenden' });
    const authFields: AuthSchemaField[] = [field, { ...field, name: 'consent', fieldType: 'CHECKBOX' }];
    expect(collectAuthFieldErrors(authFields, { website: ':::', consent: false },
      (f) => messageFor(f, 'fieldRequired'), (f) => messageFor(f, 'fieldMustBeTrue'), messageFor,
    )).toEqual({
      website: 'Website muss eine gültige URL sein',
      consent: i18n.t('workspace.actions.validation.fieldMustBeTrue', { field: 'Website' }),
    });
  });
});
