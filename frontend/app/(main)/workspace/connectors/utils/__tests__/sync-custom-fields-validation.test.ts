import { describe, expect, it } from 'vitest';
import { createInstance } from 'i18next';
import {
  collectSyncCustomFieldErrors,
  isSyncCustomFieldShown,
  syncCustomFieldErrorMessage,
  validateSyncCustomField,
} from '../sync-custom-fields-validation';
import { collectAuthFieldErrors } from '../../components/authenticate-tab/auth-step-validation';
import { CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME } from '../../constants';
import type { SyncCustomField, AuthSchemaField } from '../../types';
import de from '@/lib/i18n/locales/de-DE.json';
import en from '@/lib/i18n/locales/en-US.json';

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

const maxPages = {
  name: 'max_pages', displayName: 'Maximum pages', fieldType: 'NUMBER', required: true,
  validation: { min: 1, max: 10000 },
} satisfies SyncCustomField;

describe('number fields', () => {
  it('checks the numeric range, not the text length', () => {
    expect(validateSyncCustomField(maxPages, 50000)).toBe('numberRange');
    expect(validateSyncCustomField(maxPages, 0)).toBe('numberRange');
    expect(validateSyncCustomField(maxPages, 2.5)).toBe('numberRange');
    expect(validateSyncCustomField(maxPages, '10000')).toBeNull();
    expect(validateSyncCustomField(maxPages, 1)).toBeNull();
    expect(validateSyncCustomField(maxPages, '')).toBe('fieldRequired');
    expect(validateSyncCustomField({ ...maxPages, validation: { min: 1 } }, 0)).toBe('numberMin');
    expect(validateSyncCustomField({ ...maxPages, validation: { max: 10 } }, 11)).toBe('numberMax');
  });

  it('ignores minLength/maxLength on number fields', () => {
    expect(validateSyncCustomField({ ...maxPages, validation: { minLength: 1, maxLength: 3 } }, 50000)).toBeNull();
  });

  it('formats the range for the reader', async () => {
    const i18n = createInstance();
    await i18n.init({ lng: 'en-US', resources: { 'en-US': { translation: en } } });
    expect(syncCustomFieldErrorMessage(i18n.t.bind(i18n), maxPages, 'numberRange'))
      .toBe('Enter a whole number from 1 to 10,000.');
  });
});

describe('showWhen', () => {
  const type = { name: 'type', displayName: 'Crawl', fieldType: 'SELECT', defaultValue: 'recursive' } satisfies SyncCustomField;
  const restrict = { name: 'restrict', displayName: 'Stay', fieldType: 'BOOLEAN', defaultValue: 'false' } satisfies SyncCustomField;
  const depth = {
    name: 'depth', displayName: 'Depth', fieldType: 'NUMBER', required: true,
    validation: { min: 1, max: 10 }, showWhen: { type: ['recursive'] },
  } satisfies SyncCustomField;

  it('shows a field only while every condition holds, with defaults and loose booleans', () => {
    expect(isSyncCustomFieldShown(depth, { type: 'single' })).toBe(false);
    expect(isSyncCustomFieldShown(depth, { type: 'recursive' })).toBe(true);
    expect(isSyncCustomFieldShown(depth, {}, [type, depth])).toBe(true);
    const both = { ...depth, showWhen: { type: ['recursive'], restrict: [true] } };
    expect(isSyncCustomFieldShown(both, { type: 'recursive', restrict: 'true' })).toBe(true);
    expect(isSyncCustomFieldShown(both, { type: 'recursive' }, [type, restrict])).toBe(false);
    expect(isSyncCustomFieldShown(type, {})).toBe(true);
  });

  it('does not validate a hidden field', () => {
    const messageFor = (_f: SyncCustomField, code: string) => code;
    expect(collectSyncCustomFieldErrors([type, depth], { type: 'single', depth: 99 }, messageFor)).toEqual({});
    expect(collectSyncCustomFieldErrors([type, depth], { type: 'recursive', depth: 99 }, messageFor))
      .toEqual({ depth: 'numberRange' });
  });
});
