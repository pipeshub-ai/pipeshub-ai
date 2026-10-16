import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { CustomSyncFieldsSection } from '../custom-sync-fields-section';
import { SettingsSection } from '../settings-section';
import type { LastSyncSummary, SyncCustomField, SyncCustomFieldGroup } from '../../../types';
import { installDomShims, renderInTheme } from '../../../__tests__/fixtures';

const recursiveOnly = { type: ['recursive'] };
const WEB_FIELDS: SyncCustomField[] = [
  { name: 'url', displayName: 'Start URL', fieldType: 'URL', required: true, group: 'website' },
  { name: 'type', displayName: 'Crawl', fieldType: 'SELECT', options: ['single', 'recursive'], defaultValue: 'recursive', group: 'crawl' },
  { name: 'discovery', displayName: 'Find pages using', fieldType: 'SELECT', options: ['sitemap_and_links', 'links', 'sitemap'], defaultValue: 'sitemap_and_links', group: 'crawl', showWhen: recursiveOnly },
  { name: 'restrict_to_start_path', displayName: 'Stay', fieldType: 'BOOLEAN', defaultValue: 'false', group: 'crawl', showWhen: recursiveOnly },
  { name: 'max_pages', displayName: 'Maximum pages', fieldType: 'NUMBER', defaultValue: 100, validation: { min: 1, max: 10000 }, unit: 'pages', group: 'crawl' },
  { name: 'depth', displayName: 'Depth', fieldType: 'NUMBER', defaultValue: 3, validation: { min: 1, max: 10 }, unit: 'clicks', group: 'advanced', showWhen: recursiveOnly },
  { name: 'max_size_mb', displayName: 'Max size', fieldType: 'NUMBER', defaultValue: 10, unit: 'MB', group: 'advanced' },
  { name: 'respect_robots_txt', displayName: 'Respect robots.txt', fieldType: 'BOOLEAN', defaultValue: 'true', group: 'advanced' },
];
const WEB_GROUPS: SyncCustomFieldGroup[] = [
  { name: 'website', displayName: 'Website' },
  { name: 'crawl', displayName: 'What to crawl' },
  { name: 'advanced', displayName: 'Advanced', collapsed: true },
];

function renderWeb(
  initial: Record<string, unknown>,
  props: { isNewConnector?: boolean; lastSyncSummary?: LastSyncSummary | null; errors?: Record<string, string> } = {}
) {
  const onChange = vi.fn();
  function Harness() {
    const [values, setValues] = React.useState(initial);
    return (
      <CustomSyncFieldsSection
        fields={WEB_FIELDS}
        groups={WEB_GROUPS}
        values={values}
        errors={props.errors ?? {}}
        onChange={(k, v) => {
          onChange(k, v);
          setValues((prev) => ({ ...prev, [k]: v }));
        }}
        connectorConfig={null}
        connectorType="Web"
        isNewConnector={props.isNewConnector}
        lastSyncSummary={props.lastSyncSummary}
      />
    );
  }
  renderInTheme(<Harness />);
  return onChange;
}

const DEFAULTS = {
  url: 'https://example.com/docs/', type: 'recursive', discovery: 'sitemap_and_links',
  restrict_to_start_path: 'false', max_pages: 100, depth: 3, max_size_mb: 10, respect_robots_txt: 'true',
};

beforeEach(() => installDomShims());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SettingsSection', () => {
  it('is a named group; a collapsible one starts closed with its summary and opens on click', () => {
    renderInTheme(
      <SettingsSection title="Advanced" description="Rare settings." collapsible summary="Depth: 5">
        <input aria-label="Inside" />
      </SettingsSection>
    );
    expect(screen.getByRole('group', { name: /Advanced/ })).toBeTruthy();
    const button = screen.getByRole('button', { name: /Advanced/ });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.getElementById(button.getAttribute('aria-controls')!)?.hidden).toBe(true);
    expect(screen.getByText('Depth: 5')).toBeTruthy();
    expect(screen.getByText('Rare settings.')).toBeTruthy();

    fireEvent.click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByLabelText('Inside')).toBeTruthy();
    expect(screen.queryByText('Depth: 5')).toBeNull();
  });

  it('stays open while forced', () => {
    renderInTheme(
      <SettingsSection title="Advanced" collapsible forceOpen>
        <span>child</span>
      </SettingsSection>
    );
    expect(screen.getByRole('button', { name: /Advanced/ }).getAttribute('aria-expanded')).toBe('true');
  });
});

describe('CustomSyncFieldsSection: Web', () => {
  it('renders groups in order with translated labels and no "Additional settings" block', () => {
    renderWeb(DEFAULTS);
    const groups = screen.getAllByRole('group').map((g) => g.querySelector('legend')?.textContent);
    expect(groups).toEqual(['Website', 'What to crawl', 'Advanced']);
    expect(screen.queryByText('Additional Settings')).toBeNull();
    expect(screen.getByText('Stay in this section')).toBeTruthy();
    expect(screen.getByText('pages')).toBeTruthy();
  });

  it('summarises changed advanced settings while collapsed and opens on an error inside', () => {
    renderWeb({ ...DEFAULTS, max_size_mb: 20, respect_robots_txt: false });
    expect(screen.getByText('Largest file to download: 20 MB · Respect robots.txt off')).toBeTruthy();
    cleanup();
    renderWeb(DEFAULTS, { errors: { depth: 'Enter a whole number from 1 to 10.' } });
    expect(screen.getByRole('button', { name: /Advanced/ }).getAttribute('aria-expanded')).toBe('true');
  });

  it('hides fields whose showWhen does not hold', () => {
    renderWeb({ ...DEFAULTS, type: 'single' });
    expect(screen.queryByText('Find pages using')).toBeNull();
    expect(screen.queryByText('Stay in this section')).toBeNull();
    expect(screen.getByText('Maximum pages')).toBeTruthy();
  });

  it('applies a preset with typed values, then shows Custom after a manual edit', () => {
    const onChange = renderWeb(DEFAULTS, { isNewConnector: true });
    const presets = screen.getByRole('radiogroup', { name: 'Start from' });
    // Untouched defaults: nothing chosen yet, and no "Custom".
    expect(within(presets).queryByRole('radio', { name: 'Custom' })).toBeNull();
    expect(within(presets).getAllByRole('radio').every((r) => r.getAttribute('aria-checked') !== 'true')).toBe(true);
    expect(screen.getByText('Suggested for this address: Docs site')).toBeTruthy();

    fireEvent.click(within(presets).getByRole('radio', { name: 'Docs site' }));
    expect(onChange).toHaveBeenCalledWith('restrict_to_start_path', true);
    expect(onChange).toHaveBeenCalledWith('depth', 5);
    expect(onChange).toHaveBeenCalledWith('max_pages', 1000);
    expect(within(presets).getByRole('radio', { name: 'Docs site' }).getAttribute('aria-checked')).toBe('true');
    expect(within(presets).queryByRole('radio', { name: 'Custom' })).toBeNull();

    fireEvent.change(screen.getByLabelText(/Maximum pages/), { target: { value: '50' } });
    expect(within(presets).getByRole('radio', { name: 'Custom' }).getAttribute('aria-checked')).toBe('true');

    onChange.mockClear();
    fireEvent.click(within(presets).getByRole('radio', { name: 'Whole site' }));
    expect(screen.getByText('Replace your current settings?')).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(onChange).toHaveBeenCalledWith('restrict_to_start_path', false);
    expect(onChange).toHaveBeenCalledWith('depth', 3);
  });

  it('shows the last sync against the page limit, and nothing without a run', () => {
    renderWeb({ ...DEFAULTS, max_pages: 1000 }, {
      lastSyncSummary: { pagesSaved: 2, pagesStored: 950, stopReason: 'max_pages', skippedMaxPages: 1204 },
    });
    expect(screen.getByText('Last sync: 950 pages stored · stopped at this limit · 1,204 left out')).toBeTruthy();
    expect(screen.getByText(/at least 90% of this limit/)).toBeTruthy();
    cleanup();
    renderWeb(DEFAULTS, { lastSyncSummary: null });
    expect(screen.queryByText(/Last sync:/)).toBeNull();
  });
});

describe('CustomSyncFieldsSection: other connectors', () => {
  it('keeps ungrouped fields in the Additional Settings block with schema text', () => {
    renderInTheme(
      <CustomSyncFieldsSection
        fields={[{ name: 'feed', displayName: 'Feed URL', fieldType: 'URL', required: true }]}
        values={{}}
        errors={{}}
        onChange={() => {}}
        connectorConfig={null}
        connectorType="RSS"
      />
    );
    expect(screen.getByText('Additional Settings')).toBeTruthy();
    expect(screen.getByText('Feed URL')).toBeTruthy();
    expect(screen.queryByRole('radiogroup')).toBeNull();
  });
});
