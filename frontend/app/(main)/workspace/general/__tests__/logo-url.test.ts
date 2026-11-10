import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { clearOrgWithLogoCache, fetchOrgWithLogo } from '@/chat/api';
import { OrgApi } from '../api';
import { blobToDataUrl } from '@/lib/utils/blob-to-data-url';
import { clearSessionCache } from '@/lib/api/session-cache';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const mockedGet = vi.mocked(apiClient.get);
const svg = '<svg xmlns="http://www.w3.org/2000/svg"><x:script xmlns:x="http://www.w3.org/2000/svg">alert(1)</x:script></svg>';
const svgBlob = () => new Blob([svg], { type: 'image/svg+xml' });

describe('org logo URLs', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('blobToDataUrl keeps the type and bytes', async () => {
    const url = await blobToDataUrl(svgBlob());
    expect(url.startsWith('data:image/svg+xml;base64,')).toBe(true);
    expect(atob(url.split(',')[1])).toBe(svg);
  });

  // A blob: URL opened in its own tab is a document on the app's origin.
  it('the edition sidebar helper gets the logo as a data URL, not a blob URL', async () => {
    clearOrgWithLogoCache();
    mockedGet
      .mockResolvedValueOnce({ status: 200, data: { _id: 'o1', registeredName: 'Acme' } })
      .mockResolvedValueOnce({ status: 200, data: svgBlob() });
    const { logoUrl } = await fetchOrgWithLogo();
    expect(logoUrl?.startsWith('data:image/svg+xml')).toBe(true);
  });

  it('the edition sidebar helper gets null when no logo is set', async () => {
    clearOrgWithLogoCache();
    mockedGet
      .mockResolvedValueOnce({ status: 200, data: { _id: 'o1', registeredName: 'Acme' } })
      .mockResolvedValueOnce({ status: 204, data: new Blob([]) });
    expect((await fetchOrgWithLogo()).logoUrl).toBeNull();
  });

  it("the edition sidebar helper does not hand the next user the last session's pending read", async () => {
    clearOrgWithLogoCache();
    mockedGet.mockReturnValue(new Promise(() => undefined));
    const lastSession = fetchOrgWithLogo();

    clearSessionCache();

    expect(fetchOrgWithLogo()).not.toBe(lastSession);
    expect(mockedGet).toHaveBeenCalledTimes(4);
  });

  it('the edition sidebar helper asks again after a failed org read', async () => {
    clearOrgWithLogoCache();
    mockedGet.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce({ status: 204, data: new Blob([]) });
    expect((await fetchOrgWithLogo()).org).toBeNull();

    mockedGet
      .mockResolvedValueOnce({ status: 200, data: { _id: 'o1', registeredName: 'Acme' } })
      .mockResolvedValueOnce({ status: 204, data: new Blob([]) });
    expect((await fetchOrgWithLogo()).org?.registeredName).toBe('Acme');
  });

  it('workspace settings get the logo as a data URL, not a blob URL', async () => {
    mockedGet.mockResolvedValueOnce({ status: 200, data: svgBlob() });
    const url = await OrgApi.getLogoUrl();
    expect(url?.startsWith('data:image/svg+xml')).toBe(true);
  });

  it('workspace settings still return null when no logo is set', async () => {
    mockedGet.mockResolvedValueOnce({ status: 204, data: new Blob([]) });
    expect(await OrgApi.getLogoUrl()).toBeNull();
  });
});
