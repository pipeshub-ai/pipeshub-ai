import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} />,
}));

const UPLOADED = 'data:image/png;base64,iVBORw0KGgo=';
const getLogos = vi.fn(async () => ({ logos: { 'custom-1': UPLOADED } }));
vi.mock('../api', () => ({ McpServersApi: { getLogos: () => getLogos() } }));

import { McpServerIcon } from '../components';
import { ensureMcpLogosLoaded, resetMcpLogos } from '../logo-store';

beforeEach(resetMcpLogos);
afterEach(cleanup);

/** Which logo is shown, by its alt text ("atlassian icon"). */
function logo(ui: React.ReactElement): string | null {
  return render(ui).container.querySelector('img')?.getAttribute('alt') ?? null;
}

describe('McpServerIcon', () => {
  it("shows a catalog server's own logo", () => {
    expect(logo(<McpServerIcon typeId="atlassian_rovo" name="Atlassian Rovo" />)).toBe('atlassian icon');
    expect(logo(<McpServerIcon typeId="stripe" name="Stripe" />)).toBe('stripe icon');
  });

  it("shows Microsoft Learn's own logo, not the broad Microsoft match", () => {
    expect(logo(<McpServerIcon typeId="microsoft_learn" name="Microsoft Learn" />)).toBe('microsoft-learn icon');
  });

  it('shows the DocuSign and Okta catalog logos', () => {
    expect(logo(<McpServerIcon typeId="docusign" name="DocuSign" />)).toBe('docusign icon');
    expect(logo(<McpServerIcon typeId="okta" name="Okta" />)).toBe('okta icon');
  });

  it('goes by the catalog type when the server was renamed', () => {
    expect(logo(<McpServerIcon typeId="linear" name="Team tracker" />)).toBe('linear icon');
  });

  it('shows the logo of the service a custom server is named after', () => {
    expect(logo(<McpServerIcon typeId={null} name="Notion" />)).toBe('notion icon');
  });

  it("shows Context7's logo", () => {
    expect(logo(<McpServerIcon typeId="context7" name="Context7" />)).toBe('context7 icon');
  });

  it("shows Zoom's catalog logo", () => {
    expect(logo(<McpServerIcon typeId="zoom" name="Zoom" />)).toBe('zoom icon');
  });

  it("shows Hugging Face's logo, also for a custom server named after it", () => {
    expect(logo(<McpServerIcon typeId="huggingface" name="Hugging Face" />)).toBe('huggingface icon');
    expect(logo(<McpServerIcon typeId={null} name="Hugging Face" />)).toBe('huggingface icon');
  });

  it('shows the logo uploaded for a custom server, over the one its name would find', async () => {
    const { container } = render(<McpServerIcon instanceId="custom-1" name="Notion" size={18} />);
    await act(() => ensureMcpLogosLoaded());

    const image = container.querySelector('img');
    expect(image?.getAttribute('src')).toBe(UPLOADED);
    expect(image?.getAttribute('width')).toBe('18');
  });

  it('finds the logo as before for a server with nothing uploaded', async () => {
    const { container } = render(<McpServerIcon instanceId="custom-2" name="Notion" />);
    await act(() => ensureMcpLogosLoaded());

    expect(container.querySelector('img')?.getAttribute('alt')).toBe('notion icon');
  });

  it('keeps the MCP mark for a custom server with no logo, not a stock icon', () => {
    const { container } = render(<McpServerIcon name="Acme internal" />);

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('[data-icon="hub"]')).toBeTruthy();
  });

  it("shows Vercel's logo", () => {
    expect(logo(<McpServerIcon typeId="vercel" name="Vercel" />)).toBe('vercel icon');
  });

});
