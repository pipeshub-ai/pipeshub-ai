import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} />,
}));

import { McpServerIcon } from '../components';

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

  it('goes by the catalog type when the server was renamed', () => {
    expect(logo(<McpServerIcon typeId="linear" name="Team tracker" />)).toBe('linear icon');
  });

  it('shows the logo of the service a custom server is named after', () => {
    expect(logo(<McpServerIcon typeId={null} name="Notion" />)).toBe('notion icon');
  });

  it("shows Context7's logo", () => {
    expect(logo(<McpServerIcon typeId="context7" name="Context7" />)).toBe('context7 icon');
  });

  it("shows Hugging Face's logo, also for a custom server named after it", () => {
    expect(logo(<McpServerIcon typeId="huggingface" name="Hugging Face" />)).toBe('huggingface icon');
    expect(logo(<McpServerIcon typeId={null} name="Hugging Face" />)).toBe('huggingface icon');
  });

  it('keeps the MCP mark for a custom server with no logo, not a stock icon', () => {
    const { container } = render(<McpServerIcon name="Acme internal" />);

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('[data-icon="hub"]')).toBeTruthy();
  });
});
