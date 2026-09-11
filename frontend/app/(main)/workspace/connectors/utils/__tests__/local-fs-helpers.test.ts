import { describe, it, expect } from 'vitest';
import {
  isDesktopOfflineError,
  localFsDesktopToast,
  readDesktopRefusal,
} from '../local-fs-helpers';
import {
  LOCAL_FS_DESKTOP_OFFLINE_TOAST_TITLE,
  LOCAL_FS_DESKTOP_UNCLAIMED_TOAST_TITLE,
} from '../../constants';

describe('isDesktopOfflineError', () => {
  it('matches the code Node puts in details on a DESKTOP_OFFLINE 409', () => {
    const processed = {
      type: 'CONFLICT',
      message: 'No desktop is connected for connector c1.',
      statusCode: 409,
      details: { code: 'DESKTOP_OFFLINE', connectorId: 'c1', retryable: true },
    };
    expect(isDesktopOfflineError(processed)).toBe(true);
  });

  it('does not treat a "sync already running" 409 as an offline desktop', () => {
    const processed = {
      type: 'CONFLICT',
      message: 'A sync is already in progress. Please wait and try again.',
      statusCode: 409,
      details: undefined,
    };
    expect(isDesktopOfflineError(processed)).toBe(false);
  });

  it('ignores the code anywhere but details', () => {
    expect(isDesktopOfflineError({ code: 'DESKTOP_OFFLINE', message: 'DESKTOP_OFFLINE' })).toBe(false);
    expect(isDesktopOfflineError(new Error('DESKTOP_OFFLINE'))).toBe(false);
    expect(isDesktopOfflineError(null)).toBe(false);
    expect(isDesktopOfflineError(undefined)).toBe(false);
  });
  it('also matches the first-enable refusal, which callers render themselves', () => {
    const processed = {
      type: 'CONFLICT',
      message: 'Connector c1 has not been set up on a desktop yet.',
      statusCode: 409,
      details: { code: 'DESKTOP_UNCLAIMED', connectorId: 'c1', retryable: true },
    };
    expect(isDesktopOfflineError(processed)).toBe(true);
  });

  it('leaves other connector failures to the generic error toast', () => {
    // isDesktopOfflineError doubles as the axios suppressErrorToast predicate,
    // so a false here is what keeps "Invalid credentials" reaching the user.
    const invalidCredentials = {
      type: 'VALIDATION_ERROR',
      message: 'Invalid credentials',
      statusCode: 400,
      details: undefined,
    };
    expect(isDesktopOfflineError(invalidCredentials)).toBe(false);
    expect(isDesktopOfflineError({ statusCode: 500, message: 'Backend error' })).toBe(false);
  });
});

describe('readDesktopRefusal', () => {
  it('maps each Node code to its reason and everything else to null', () => {
    expect(readDesktopRefusal({ details: { code: 'DESKTOP_OFFLINE' } })).toBe('offline');
    expect(readDesktopRefusal({ details: { code: 'DESKTOP_UNCLAIMED' } })).toBe('unclaimed');
    expect(readDesktopRefusal({ details: { code: 'HTTP_CONFLICT' } })).toBeNull();
    expect(readDesktopRefusal({ message: 'DESKTOP_OFFLINE' })).toBeNull();
    expect(readDesktopRefusal(undefined)).toBeNull();
  });
});

describe('localFsDesktopToast', () => {
  it('picks the first-enable wording for an unclaimed refusal', () => {
    const toast = localFsDesktopToast({ reason: 'unclaimed' });
    expect(toast.variant).toBe('info');
    expect(toast.title).toBe(LOCAL_FS_DESKTOP_UNCLAIMED_TOAST_TITLE);
  });

  it('keeps the open-the-app wording for an offline refusal', () => {
    expect(localFsDesktopToast({ reason: 'offline' }).title).toBe(LOCAL_FS_DESKTOP_OFFLINE_TOAST_TITLE);
  });
});
