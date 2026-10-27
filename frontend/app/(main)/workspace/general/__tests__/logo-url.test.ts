import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { fetchOrgLogo } from '@/chat/api';
import { OrgApi } from '../api';
import { blobToDataUrl } from '@/lib/utils/blob-to-data-url';

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
  it('the chat sidebar gets the logo as a data URL, not a blob URL', async () => {
    mockedGet.mockResolvedValueOnce({ status: 200, data: svgBlob() });
    const url = await fetchOrgLogo();
    expect(url?.startsWith('data:image/svg+xml')).toBe(true);
  });

  it('the chat sidebar gets null when no logo is set', async () => {
    mockedGet.mockResolvedValueOnce({ status: 204, data: new Blob([]) });
    expect(await fetchOrgLogo()).toBeNull();
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
