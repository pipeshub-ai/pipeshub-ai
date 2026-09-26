import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import type { ConnectorSchemaResponse, FilterOptionsResponse, FilterSchemaField } from '../../../types';

const getFilterFieldOptions = vi.fn<
  (connectorId: string, filterKey: string, params?: Record<string, unknown>) => Promise<FilterOptionsResponse>
>();

vi.mock('@/app/(main)/workspace/connectors/api', () => ({
  ConnectorsApi: {
    getFilterFieldOptions: (connectorId: string, filterKey: string, params?: Record<string, unknown>) =>
      getFilterFieldOptions(connectorId, filterKey, params),
  },
}));

// Icons render their ligature name as text, which would leak into accessible names.
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

// The real picker is a calendar; the filter row only cares about what it hands back on Apply / Clear.
vi.mock('@/app/components/ui/date-range-picker', () => ({
  DateRangePicker: (props: {
    label: string;
    dateType: string;
    startDate?: string;
    endDate?: string;
    onApply: (start: string, end: string | undefined, type: string) => void;
    onClear: () => void;
  }) => (
    <div>
      <span>{props.label}</span>
      <span data-testid="picker-range">{`${props.startDate ?? ''}|${props.endDate ?? ''}`}</span>
      <button type="button" onClick={() => props.onApply('2026-01-02T10:00', '2026-01-05T18:30', props.dateType)}>
        Apply dates
      </button>
      <button type="button" onClick={props.onClear}>
        Clear dates
      </button>
    </div>
  ),
}));

import '@/lib/__tests__/test-i18n';
import { FiltersSection } from '../filters-section';
import { useConnectorsStore } from '../../../store';

afterEach(() => cleanup());

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', NoopResizeObserver);
  Element.prototype.scrollIntoView = () => {};
  getFilterFieldOptions.mockReset();
  useConnectorsStore.getState().reset();
});

function page(options: { id: string; label: string }[], extra: Partial<FilterOptionsResponse> = {}): FilterOptionsResponse {
  return { success: true, options, page: 1, limit: 20, hasMore: false, ...extra };
}

function setup({
  sync = [],
  indexing = [],
  syncValues = {},
  indexingValues = {},
  connectorId = 'conn-1',
  readOnly = false,
}: {
  sync?: FilterSchemaField[];
  indexing?: FilterSchemaField[];
  syncValues?: Record<string, unknown>;
  indexingValues?: Record<string, unknown>;
  connectorId?: string | null;
  readOnly?: boolean;
}) {
  const schema = {
    filters: {
      sync: { schema: { fields: sync } },
      indexing: { schema: { fields: indexing } },
    },
  } as unknown as ConnectorSchemaResponse['schema'];
  const base = useConnectorsStore.getState().formData;
  useConnectorsStore.setState({
    connectorSchema: schema,
    panelConnectorId: connectorId,
    formData: { ...base, filters: { sync: { ...syncValues }, indexing: { ...indexingValues } } },
  });
  return render(
    <Theme>
      <FiltersSection readOnly={readOnly} />
    </Theme>,
  );
}

const syncValue = (name: string) => useConnectorsStore.getState().formData.filters.sync[name];
const indexingValue = (name: string) => useConnectorsStore.getState().formData.filters.indexing[name];

const spaces: FilterSchemaField = {
  name: 'space_keys',
  displayName: 'Spaces',
  filterType: 'list',
  operators: ['in', 'not_in'],
  optionSourceType: 'static',
  options: [
    { id: 'ENG', label: 'Engineering' },
    { id: 'OPS', label: 'Operations' },
  ],
};

const modified: FilterSchemaField = {
  name: 'modified',
  displayName: 'Modified',
  filterType: 'datetime',
  operators: ['is_between', 'is_after', 'is_before', 'last_7_days'],
};

describe('FiltersSection — layout', () => {
  it('shows nothing when the connector has no filters at all', () => {
    const { container } = setup({});
    expect(container.textContent).toBe('');
  });

  it('shows a required sync filter from the start and keeps it off the "Add filter" menu', async () => {
    setup({ sync: [{ ...spaces, required: true }, modified] });

    expect(screen.getByText('Sync filters')).toBeTruthy();
    expect(screen.getByText('Finish operator and value for each filter below to see a summary here.')).toBeTruthy();
    // A required filter has no Clear button: the connector cannot run without it.
    expect(screen.queryByRole('button', { name: /clear/i })).toBeNull();

    fireEvent.keyDown(screen.getByRole('button', { name: /add filter/i }), { key: 'Enter' });
    const items = await screen.findAllByRole('menuitem');
    expect(items.map((i) => i.textContent)).toEqual(['Modified']);
  });

  it('asks the user to add a filter when no sync filter is set yet', () => {
    setup({ sync: [spaces] });
    expect(screen.getByText('No filters yet. Use "Add filter" to choose one of the supported filters.')).toBeTruthy();
  });

  it('seeds a required sync filter again after it was cleared', () => {
    setup({
      sync: [{ ...spaces, required: true, defaultOperator: 'in' }],
      syncValues: { space_keys: null },
    });
    expect(syncValue('space_keys')).toEqual({ operator: 'in', value: [], type: 'list' });
  });

  it('keeps a cleared optional sync filter cleared', () => {
    setup({ sync: [{ ...spaces, defaultOperator: 'in' }], syncValues: { space_keys: null } });
    expect(syncValue('space_keys')).toBeNull();
    expect(screen.queryByText('Spaces')).toBeNull();
  });

  it('disables every control and hides "Add filter" when read-only', () => {
    const { container } = setup({ sync: [spaces], readOnly: true });
    expect(container.querySelector('fieldset')?.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: /add filter/i })).toBeNull();
  });
});

describe('FiltersSection — adding and clearing a sync filter', () => {
  it('adds a filter with its first operator, then clears it as an explicit removal', async () => {
    setup({ sync: [spaces] });

    fireEvent.keyDown(screen.getByRole('button', { name: /add filter/i }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Spaces' }));

    expect(syncValue('space_keys')).toEqual({ operator: 'in', value: [], type: 'list' });
    expect(screen.getByRole('button', { name: 'Select Spaces' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /clear/i }));

    expect(syncValue('space_keys')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Select Spaces' })).toBeNull();
  });

  it('saves the picked static values with their labels and summarises them', async () => {
    setup({ sync: [{ ...spaces, required: true }], syncValues: { space_keys: { operator: 'in', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Spaces' }));
    fireEvent.click(await screen.findByText('Engineering'));

    expect(syncValue('space_keys')).toEqual({
      operator: 'in',
      value: [{ id: 'ENG', label: 'Engineering' }],
      type: 'list',
    });
    expect(screen.getByText('Spaces · In')).toBeTruthy();
    expect(screen.getAllByText('Engineering').length).toBeGreaterThan(0);
  });

  it('keeps an operator saved by the API even when the schema does not list it', () => {
    setup({
      sync: [{ name: 'title', displayName: 'Title', filterType: 'string', operators: ['is', 'is_not'] }],
      syncValues: { title: { operator: 'equals', value: 'Roadmap' } },
    });
    expect(screen.getByText('Title: Equals · Roadmap')).toBeTruthy();
    expect(screen.getByRole('combobox').textContent).toBe('Equals');
  });
});

describe('FiltersSection — dynamic options', () => {
  const repos: FilterSchemaField = {
    name: 'repo_ids',
    displayName: 'Repositories',
    filterType: 'list',
    operators: ['in'],
    optionSourceType: 'dynamic',
    required: true,
  };

  it('loads the first page of options when the list opens and saves a pick', async () => {
    getFilterFieldOptions.mockResolvedValue(page([{ id: 'r1', label: 'acme/api' }]));
    setup({ sync: [repos], syncValues: { repo_ids: { operator: 'in', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Repositories' }));
    fireEvent.click(await screen.findByText('acme/api'));

    expect(getFilterFieldOptions).toHaveBeenCalledWith('conn-1', 'repo_ids', { limit: 20, page: 1 });
    expect(syncValue('repo_ids')).toEqual({
      operator: 'in',
      value: [{ id: 'r1', label: 'acme/api' }],
      type: 'list',
    });
  });

  it("shows the server's message when there are no options", async () => {
    getFilterFieldOptions.mockResolvedValue(page([], { message: 'This account has no repositories yet.' }));
    setup({ sync: [repos], syncValues: { repo_ids: { operator: 'in', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Repositories' }));

    expect(await screen.findByText('This account has no repositories yet.')).toBeTruthy();
  });

  it('says the options could not be loaded, instead of "No results found", when the request fails', async () => {
    getFilterFieldOptions.mockRejectedValue(new Error('Network Error'));
    setup({ sync: [repos], syncValues: { repo_ids: { operator: 'in', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Repositories' }));

    expect(
      await screen.findByText("We couldn't load the options for this filter. Close this list and open it again to retry."),
    ).toBeTruthy();
    expect(screen.queryByText('No results found')).toBeNull();
  });

  it('only offers repositories under the organisations picked in the sync filters', async () => {
    getFilterFieldOptions.mockResolvedValue(page([]));
    setup({
      sync: [
        { name: 'org_ids', displayName: 'Organisations', filterType: 'list', operators: ['in', 'not_in'], optionSourceType: 'dynamic' },
        repos,
      ],
      syncValues: {
        org_ids: { operator: 'not_in', value: ['legacy-org'] },
        repo_ids: { operator: 'in', value: [] },
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Select Repositories' }));

    await waitFor(() =>
      expect(getFilterFieldOptions).toHaveBeenCalledWith('conn-1', 'repo_ids', {
        limit: 20,
        page: 1,
        excludeContextGroupPath: ['legacy-org'],
      }),
    );
  });

  it('searches on the server and ignores a slower, older answer', async () => {
    let resolveFirst: (v: FilterOptionsResponse) => void = () => {};
    getFilterFieldOptions
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }))
      .mockResolvedValueOnce(page([{ id: 'r2', label: 'acme/web' }]));
    setup({ sync: [repos], syncValues: { repo_ids: { operator: 'in', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Repositories' }));
    vi.useFakeTimers();
    try {
      fireEvent.change(screen.getByPlaceholderText('Search'), { target: { value: 'web' } });
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
    } finally {
      vi.useRealTimers();
    }
    await act(async () => {
      resolveFirst(page([{ id: 'r1', label: 'acme/api' }]));
    });

    expect(getFilterFieldOptions).toHaveBeenLastCalledWith('conn-1', 'repo_ids', { limit: 20, search: 'web', page: 1 });
    expect(await screen.findByText('acme/web')).toBeTruthy();
    expect(screen.queryByText('acme/api')).toBeNull();
  });
});

describe('FiltersSection — single choice, free text, booleans, numbers', () => {
  const project: FilterSchemaField = {
    name: 'project',
    displayName: 'Project',
    filterType: 'select',
    optionSourceType: 'static',
    required: true,
    options: [
      { id: 'p1', label: 'Apollo' },
      { id: 'p2', label: 'Gemini' },
    ],
  };

  it('picks one project from a searchable list and shows it on the button', async () => {
    setup({ sync: [project], syncValues: { project: { operator: 'is', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Project' }));
    fireEvent.change(await screen.findByPlaceholderText('Search'), { target: { value: 'gem' } });
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Gemini']);
    fireEvent.click(screen.getByRole('option', { name: 'Gemini' }));

    expect(syncValue('project')).toEqual({ operator: 'is', value: [{ id: 'p2', label: 'Gemini' }], type: 'select' });
    expect(screen.getByRole('button', { name: 'Gemini' })).toBeTruthy();
    expect(screen.getByText('Project: Gemini')).toBeTruthy();
  });

  it('shows "No results found" when the search matches nothing', async () => {
    setup({ sync: [project], syncValues: { project: { operator: 'is', value: [] } } });
    fireEvent.click(screen.getByRole('button', { name: 'Select Project' }));
    fireEvent.change(await screen.findByPlaceholderText('Search'), { target: { value: 'zzz' } });
    expect(screen.getByText('No results found')).toBeTruthy();
  });

  it('lets the user type free-form values when the filter has no option list', () => {
    setup({
      sync: [{ name: 'folder_ids', displayName: 'Folder IDs', filterType: 'list', operators: ['in'], optionSourceType: 'manual', required: true }],
      syncValues: { folder_ids: { operator: 'in', value: ['f-1'] } },
    });

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'f-2' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(syncValue('folder_ids')).toEqual({ operator: 'in', value: ['f-1', 'f-2'], type: 'list' });
  });

  it('shows a sync boolean with a real default as set, and saves the untick', () => {
    setup({
      sync: [{ name: 'include_archived', displayName: 'Include archived', filterType: 'boolean', defaultValue: true }],
    });

    const box = screen.getByRole('checkbox');
    expect(box.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Include archived: Is · Yes')).toBeTruthy();

    fireEvent.click(box);
    expect(syncValue('include_archived')).toEqual({ operator: 'is', value: false, type: 'boolean' });
  });

  it('does not pretend a sync boolean without a default was chosen', () => {
    setup({ sync: [{ name: 'include_archived', displayName: 'Include archived', filterType: 'boolean' }] });
    expect(syncValue('include_archived')).toBeUndefined();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('saves a typed number, and an emptied box as no value', () => {
    setup({
      sync: [{ name: 'max_size', displayName: 'Max size', filterType: 'number', operators: ['less_than'], required: true }],
      syncValues: { max_size: { operator: 'less_than', value: '' } },
    });

    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '25' } });
    expect(syncValue('max_size')).toEqual({ operator: 'less_than', value: 25, type: 'number' });

    fireEvent.change(input, { target: { value: '' } });
    expect(syncValue('max_size')).toEqual({ operator: 'less_than', value: null, type: 'number' });
  });

  it('saves typed text and summarises it', () => {
    setup({
      sync: [{ name: 'title', displayName: 'Title', filterType: 'string', operators: ['contains'], required: true }],
      syncValues: { title: { operator: 'contains', value: '' } },
    });

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Q3 plan' } });

    expect(syncValue('title')).toEqual({ operator: 'contains', value: 'Q3 plan', type: 'string' });
    expect(screen.getByText('Title: Contains · Q3 plan')).toBeTruthy();
  });

  it('lets the user type an operator when the schema offers none', () => {
    setup({
      sync: [{ name: 'label', displayName: 'Label', filterType: 'string', required: true, noImplicitOperatorDefault: true }],
      syncValues: { label: { operator: '', value: 'x' } },
    });

    const operatorBox = screen.getByPlaceholderText('Operator');
    fireEvent.change(operatorBox, { target: { value: 'starts_with' } });
    expect(syncValue('label')).toEqual({ operator: 'starts_with', value: 'x', type: 'string' });
  });
});

describe('FiltersSection — single choice from a live list', () => {
  const space: FilterSchemaField = {
    name: 'space',
    displayName: 'Space',
    filterType: 'select',
    optionSourceType: 'dynamic',
    required: true,
  };

  it('loads the next page when the user scrolls to the end of the list', async () => {
    getFilterFieldOptions
      .mockResolvedValueOnce(page([{ id: 's1', label: 'Design' }], { hasMore: true, cursor: 'next-1' }))
      .mockResolvedValueOnce(page([{ id: 's2', label: 'Finance' }]));
    setup({ sync: [space], syncValues: { space: { operator: 'is', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Space' }));
    const list = await screen.findByRole('listbox');
    await screen.findByText('Design');
    Object.defineProperty(list, 'scrollHeight', { configurable: true, value: 500 });
    Object.defineProperty(list, 'clientHeight', { configurable: true, value: 240 });
    list.scrollTop = 250;
    fireEvent.scroll(list);

    expect(await screen.findByText('Finance')).toBeTruthy();
    expect(getFilterFieldOptions).toHaveBeenLastCalledWith('conn-1', 'space', { limit: 20, cursor: 'next-1' });
    fireEvent.click(screen.getByRole('option', { name: 'Finance' }));
    expect(syncValue('space')).toEqual({ operator: 'is', value: [{ id: 's2', label: 'Finance' }], type: 'select' });
  });

  it('says the options could not be loaded when the request fails', async () => {
    getFilterFieldOptions.mockRejectedValue(new Error('timeout of 300000ms exceeded'));
    setup({ sync: [space], syncValues: { space: { operator: 'is', value: [] } } });

    fireEvent.click(screen.getByRole('button', { name: 'Select Space' }));

    expect(
      await screen.findByText("We couldn't load the options for this filter. Close this list and open it again to retry."),
    ).toBeTruthy();
  });
});

describe('FiltersSection — dates', () => {
  it('explains that a rolling window has no dates to pick', () => {
    setup({ sync: [modified], syncValues: { modified: { operator: 'last_7_days', value: null } } });
    expect(screen.getByText('This operator uses a rolling window — no fixed dates to set.')).toBeTruthy();
    expect(screen.getByText('Modified: Last 7 Days')).toBeTruthy();
  });

  it('saves a picked date range as timestamps and shows it back', () => {
    setup({ sync: [{ ...modified, required: true }], syncValues: { modified: { operator: 'is_between', value: { start: null, end: null } } } });

    fireEvent.click(screen.getByText('Apply dates'));

    expect(syncValue('modified')).toEqual({
      operator: 'is_between',
      value: {
        start: new Date('2026-01-02T10:00').getTime(),
        end: new Date('2026-01-05T18:30').getTime(),
      },
      type: 'datetime',
    });
    expect(screen.getByTestId('picker-range').textContent).toBe('2026-01-02T10:00|2026-01-05T18:30');
    expect(screen.getByText('Modified: Is Between · date range')).toBeTruthy();

    fireEvent.click(screen.getByText('Clear dates'));
    expect(syncValue('modified')).toEqual({ operator: 'is_between', value: { start: null, end: null }, type: 'datetime' });
  });

  it('keeps only the end date for "is before"', () => {
    const end = new Date('2026-03-01T09:15').getTime();
    setup({ sync: [{ ...modified, required: true }], syncValues: { modified: { operator: 'is_before', value: end } } });

    expect(screen.getByTestId('picker-range').textContent).toBe('|2026-03-01T09:15');
    fireEvent.click(screen.getByText('Apply dates'));
    expect(syncValue('modified')).toEqual({
      operator: 'is_before',
      value: { start: null, end: new Date('2026-01-05T18:30').getTime() },
      type: 'datetime',
    });
  });

  it('seeds a schema-provided date default', () => {
    const start = new Date('2025-06-01T00:00').getTime();
    setup({
      sync: [{ ...modified, defaultOperator: 'is_after', defaultValue: { operator: 'is_after', value: { start, end: null } } }],
    });
    expect(syncValue('modified')).toEqual({ operator: 'is_after', value: { start, end: null }, type: 'datetime' });
    expect(screen.getByTestId('picker-range').textContent).toBe('2025-06-01T00:00|');
  });
});

describe('FiltersSection — indexing filters and manual indexing', () => {
  const manual: FilterSchemaField = {
    name: 'enable_manual_sync',
    displayName: 'Manual indexing',
    description: 'Index only the records you pick.',
    filterType: 'boolean',
  };
  const pdfs: FilterSchemaField = {
    name: 'index_pdfs',
    displayName: 'Index PDFs',
    filterType: 'boolean',
    defaultValue: true,
  };

  it('shows manual indexing in its own card, off by default, and saves the switch', () => {
    setup({ indexing: [manual, pdfs] });

    expect(screen.getByText('Manual indexing')).toBeTruthy();
    expect(screen.getByText('Index only the records you pick.')).toBeTruthy();
    expect(indexingValue('enable_manual_sync')).toEqual({ operator: 'is', value: false, type: 'boolean' });

    const toggle = screen.getByRole('switch');
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(toggle);
    expect(indexingValue('enable_manual_sync')).toEqual({ operator: 'is', value: true, type: 'boolean' });
  });

  it('always shows indexing filters with their defaults, with no summary chips and no Clear', () => {
    setup({ indexing: [manual, pdfs] });

    expect(screen.getByText('Indexing filters')).toBeTruthy();
    expect(screen.getByText('Index PDFs')).toBeTruthy();
    expect(indexingValue('index_pdfs')).toEqual({ operator: 'is', value: true, type: 'boolean' });
    expect(screen.queryByText(/No filters yet/)).toBeNull();
    expect(screen.queryByRole('button', { name: /clear/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /add filter/i })).toBeNull();
  });

  it('locks the manual indexing switch when read-only', () => {
    setup({ indexing: [manual], readOnly: true });
    expect((screen.getByRole('switch') as HTMLButtonElement).disabled).toBe(true);
  });
});
