import { afterEach, beforeAll, describe, it, expect, vi } from 'vitest';
import { i18n } from '@/lib/i18n';
import { isElectron } from '@/lib/electron';
import {
  isDesktopOfflineError,
  isLocalFsConfigReadOnly,
  localFsDesktopToast,
  readDesktopRefusal,
} from '../local-fs-helpers';

vi.mock('@/lib/electron', () => ({ isElectron: vi.fn() }));
const mockIsElectron = vi.mocked(isElectron);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

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

  it('also matches the other-device refusal', () => {
    const processed = {
      type: 'CONFLICT',
      message: 'Connector c1 is owned by another device.',
      statusCode: 409,
      details: {
        code: 'DESKTOP_OWNED_BY_OTHER_DEVICE',
        connectorId: 'c1',
        retryable: false,
        ownerDeviceName: 'WIN-LAPTOP',
      },
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
    expect(readDesktopRefusal({ details: { code: 'DESKTOP_OFFLINE' } })).toEqual({ reason: 'offline' });
    expect(readDesktopRefusal({ details: { code: 'DESKTOP_UNCLAIMED' } })).toEqual({ reason: 'unclaimed' });
    expect(readDesktopRefusal({ details: { code: 'DESKTOP_OWNED_BY_OTHER_DEVICE' } })).toEqual({
      reason: 'other_device',
    });
    expect(readDesktopRefusal({ details: { code: 'HTTP_CONFLICT' } })).toBeNull();
    expect(readDesktopRefusal({ details: { code: 'toString' } })).toBeNull();
    expect(readDesktopRefusal({ message: 'DESKTOP_OFFLINE' })).toBeNull();
    expect(readDesktopRefusal(undefined)).toBeNull();
  });

  it('carries the owner device name from details, ignoring blanks and non-strings', () => {
    expect(
      readDesktopRefusal({
        details: { code: 'DESKTOP_OWNED_BY_OTHER_DEVICE', ownerDeviceName: ' WIN-LAPTOP ' },
      })
    ).toEqual({ reason: 'other_device', ownerDeviceName: 'WIN-LAPTOP' });
    expect(
      readDesktopRefusal({ details: { code: 'DESKTOP_OWNED_BY_OTHER_DEVICE', ownerDeviceName: '  ' } })
    ).toEqual({ reason: 'other_device' });
    expect(
      readDesktopRefusal({ details: { code: 'DESKTOP_OWNED_BY_OTHER_DEVICE', ownerDeviceName: 42 } })
    ).toEqual({ reason: 'other_device' });
  });
});

describe('localFsDesktopToast', () => {
  it('picks the first-enable wording for an unclaimed refusal', () => {
    const toast = localFsDesktopToast({ reason: 'unclaimed' });
    expect(toast.variant).toBe('info');
    expect(toast.title).toBe(i18n.t('workspace.connectors.localFsDesktop.unclaimedToast'));
    expect(toast.title).toContain('has not been set up on a desktop');
  });

  it('keeps the open-the-app wording for an offline refusal', () => {
    expect(localFsDesktopToast({ reason: 'offline' }).title).toBe(
      i18n.t('workspace.connectors.localFsDesktop.offlineToast')
    );
  });

  it('names the owner device when an offline refusal carries it', () => {
    const title = localFsDesktopToast({ reason: 'offline', ownerDeviceName: 'MAC-STUDIO' }).title;
    expect(title).toContain('MAC-STUDIO');
    expect(title).not.toBe(i18n.t('workspace.connectors.localFsDesktop.offlineToast'));
  });

  it('names the owner device for an other-device refusal', () => {
    const title = localFsDesktopToast({ reason: 'other_device', ownerDeviceName: 'WIN-LAPTOP' }).title;
    expect(title).toContain('WIN-LAPTOP');
    expect(title).not.toContain('{{');
  });

  it('falls back to generic wording when the owner name is unknown', () => {
    expect(localFsDesktopToast({ reason: 'other_device' }).title).toBe(
      i18n.t('workspace.connectors.localFsDesktop.ownedByOtherDeviceUnknownToast')
    );
  });
});

describe('isLocalFsConfigReadOnly', () => {
  afterEach(() => {
    mockIsElectron.mockReset();
  });

  it('is read-only for a Local FS connector opened in the browser', () => {
    mockIsElectron.mockReturnValue(false);
    expect(isLocalFsConfigReadOnly('LOCAL_FS')).toBe(true);
    expect(isLocalFsConfigReadOnly('local-fs')).toBe(true);
  });

  it('stays editable inside the desktop app', () => {
    mockIsElectron.mockReturnValue(true);
    expect(isLocalFsConfigReadOnly('LOCAL_FS')).toBe(false);
  });

  it('never gates a non Local FS connector', () => {
    mockIsElectron.mockReturnValue(false);
    expect(isLocalFsConfigReadOnly('GOOGLE_DRIVE')).toBe(false);
    expect(isLocalFsConfigReadOnly('')).toBe(false);
  });
});
