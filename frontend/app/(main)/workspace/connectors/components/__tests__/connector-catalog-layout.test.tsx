import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('@/config', () => ({ PermissionLockIcon: () => <span>locked</span> }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ label }: { label?: string }) => <div role="status">{label}</div>,
}));

import { ConnectorCatalogLayout } from '../connector-catalog-layout';
import { installDomShims, renderInTheme, makeConnector, makeInstance } from '../../__tests__/fixtures';

type Props = React.ComponentProps<typeof ConnectorCatalogLayout>;

const jira = makeConnector();
const slack = makeConnector({
  name: 'Slack',
  type: 'Slack',
  appGroup: 'Slack',
  appDescription: 'Sync channels and threads',
});
const drive = makeConnector({
  name: 'Google Drive',
  type: 'Drive',
  appGroup: 'Google Workspace',
  appDescription: 'Sync files and folders',
});

const tabs = [
  { value: 'all', label: 'All' },
  { value: 'configured', label: 'Configured' },
  { value: 'not_configured', label: 'Not configured' },
];

function renderCatalog(overrides: Partial<Props> = {}) {
  const props: Props = {
    title: 'Connectors',
    subtitle: 'Connect and manage integrations with external services',
    searchQuery: '',
    onSearchChange: vi.fn(),
    tabs,
    activeTab: 'all',
    onTabChange: vi.fn(),
    registryConnectors: [jira, slack, drive],
    activeConnectors: [],
    onSetup: vi.fn(),
    onAddInstance: vi.fn(),
    onCardClick: vi.fn(),
    ...overrides,
  };
  const view = renderInTheme(<ConnectorCatalogLayout {...props} />);
  return { props, ...view };
}

const cardFor = (type: string) => screen.getByTestId(`connector-card-${type}`);
const tab = (label: string) => screen.getByRole('radio', { name: label });

beforeEach(() => installDomShims());
afterEach(() => cleanup());

describe('ConnectorCatalogLayout: states', () => {
  it('shows a loader while connectors load', () => {
    renderCatalog({ isLoading: true });
    expect(screen.getByRole('status').textContent).toBe('Loading connectors…');
    expect(screen.queryByTestId('connector-card-Jira')).toBeNull();
  });

  it('says when there are no connectors to show', () => {
    renderCatalog({ registryConnectors: [] });
    expect(screen.getByText('No connectors found')).toBeTruthy();
  });

  it('lists each connector with its description', () => {
    renderCatalog();
    expect(within(cardFor('Jira')).getByText('Jira')).toBeTruthy();
    expect(within(cardFor('Slack')).getByText('Sync channels and threads')).toBeTruthy();
    expect(tab('All (3)')).toBeTruthy();
  });
});

describe('ConnectorCatalogLayout: configured connectors', () => {
  const active = [
    makeInstance({ _key: 'j1', name: 'Jira Eng', isActive: true }),
    makeInstance({ _key: 'j2', name: 'Jira Ops', isActive: false }),
  ];

  it('shows one card per type under the type name, with instance counts', () => {
    renderCatalog({ activeConnectors: active });

    const c = cardFor('Jira');
    expect(within(c).getByText('Jira')).toBeTruthy();
    expect(within(c).queryByText('Jira Eng')).toBeNull();
    expect(within(c).getByText('1 active instance')).toBeTruthy();
    expect(within(c).getByText('1 inactive instance')).toBeTruthy();
    expect(screen.getAllByTestId('connector-card-Jira')).toHaveLength(1);
  });

  it('counts configured and not-configured types for the tabs', () => {
    renderCatalog({ activeConnectors: active });
    expect(tab('Configured (1)')).toBeTruthy();
    expect(tab('Not configured (2)')).toBeTruthy();
  });

  it('shows only configured types on the Configured tab', () => {
    renderCatalog({ activeConnectors: active, activeTab: 'configured' });
    expect(cardFor('Jira')).toBeTruthy();
    expect(screen.queryByTestId('connector-card-Slack')).toBeNull();
  });

  it('opens the type page from the card and adds an instance from "+"', () => {
    const { props } = renderCatalog({ activeConnectors: active });

    fireEvent.click(cardFor('Jira'));
    expect(props.onCardClick).toHaveBeenCalledWith(expect.objectContaining({ type: 'Jira' }));

    fireEvent.click(within(cardFor('Jira')).getByRole('button', { name: 'Add Another Instance' }));
    expect(props.onAddInstance).toHaveBeenCalledWith(expect.objectContaining({ type: 'Jira' }));
    expect(props.onCardClick).toHaveBeenCalledTimes(1);
  });
});

describe('ConnectorCatalogLayout: search and tabs', () => {
  it('filters by name, description, or app group without regard to case', () => {
    const { rerender } = renderCatalog({ searchQuery: 'SLACK' });
    expect(cardFor('Slack')).toBeTruthy();
    expect(screen.queryByTestId('connector-card-Jira')).toBeNull();
    expect(tab('All (1)')).toBeTruthy();

    rerender(
      <ConnectorCatalogLayout
        title="Connectors"
        subtitle=""
        searchQuery="google workspace"
        onSearchChange={vi.fn()}
        tabs={tabs}
        activeTab="all"
        onTabChange={vi.fn()}
        registryConnectors={[jira, slack, drive]}
        activeConnectors={[]}
      />
    );
    expect(cardFor('Drive')).toBeTruthy();
    expect(screen.queryByTestId('connector-card-Slack')).toBeNull();
  });

  it('shows the empty state when nothing matches the search', () => {
    renderCatalog({ searchQuery: 'salesforce' });
    expect(screen.getByText('No connectors found')).toBeTruthy();
  });

  it('reports what the user types in the search box', () => {
    const { props } = renderCatalog();
    fireEvent.change(screen.getByPlaceholderText('Search...'), { target: { value: 'dri' } });
    expect(props.onSearchChange).toHaveBeenCalledWith('dri');
  });

  it('reports a tab change', () => {
    const { props } = renderCatalog();
    fireEvent.click(screen.getByRole('radio', { name: /Not configured/ }));
    expect(props.onTabChange).toHaveBeenCalledWith('not_configured');
  });
});

describe('ConnectorCatalogLayout: setting up', () => {
  it('starts setup from a connector that has no instances yet', () => {
    const { props } = renderCatalog();
    fireEvent.click(within(cardFor('Slack')).getByRole('button', { name: /Setup/ }));
    expect(props.onSetup).toHaveBeenCalledWith(slack);
  });

  it('shows a lock and does not start setup when the user may not create connectors', () => {
    const { props } = renderCatalog({ setupPermissionDenied: true });
    const setup = within(cardFor('Slack')).getByRole('button', { name: /Setup/ });

    expect(within(setup).getByText('locked')).toBeTruthy();
    fireEvent.click(setup);
    expect(props.onSetup).not.toHaveBeenCalled();
  });
});
