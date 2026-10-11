import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import {
  ModelPicker,
  customPicked,
  pickedFromDiscovered,
  seedTypedModels,
  type PerModelFlagField,
  type PickedDefaults,
} from '../components/model-picker';
import { canFetchModels } from '../hooks/use-model-discovery';
import { applyBatchEvent, mergeBatchRows, type BatchRow } from '../hooks/use-batch-add-models';
import type { DiscoveredModel, PickedModel } from '../types';

const models: DiscoveredModel[] = [
  {
    id: 'gpt-4o',
    displayName: 'GPT-4o',
    capabilities: ['llm'],
    contextLength: 128000,
    isMultimodal: true,
    supportsTools: true,
    isReasoning: false,
  },
  {
    id: 'whisper-1',
    displayName: 'Whisper',
    capabilities: ['stt'],
  },
  {
    id: 'custom-ft',
    displayName: 'custom-ft',
    capabilities: ['other'],
  },
];

const FLAG_FIELDS: PerModelFlagField[] = [
  { name: 'isReasoning', label: 'Reasoning' },
  { name: 'isMultimodal', label: 'Multimodal' },
];

const FORM_DEFAULTS: PickedDefaults = { isReasoning: false, isMultimodal: false, contextLength: null };

const latest = (seen: PickedModel[][], id: string) => seen[seen.length - 1]?.find((item) => item.id === id);

afterEach(cleanup);

function renderPicker(
  picked: PickedModel[] = [],
  onChange: (next: PickedModel[]) => void = () => undefined,
  options: { models?: DiscoveredModel[]; defaults?: PickedDefaults } = {},
) {
  return render(
    <Theme>
      <ModelPicker
        models={options.models ?? models}
        picked={picked}
        onChange={onChange}
        showDefault
        defaultModelId={picked[0]?.id ?? null}
        onDefaultChange={() => undefined}
        flagFields={FLAG_FIELDS}
        contextLengthLabel="Context Length"
        defaults={options.defaults ?? FORM_DEFAULTS}
      />
    </Theme>,
  );
}

describe('picked model flags', () => {
  it('uses provider metadata and falls back to the form for flags it did not report', () => {
    const formDefaults: PickedDefaults = { isReasoning: true, isMultimodal: false, contextLength: 8192 };
    expect(pickedFromDiscovered(models[0], formDefaults)).toMatchObject({
      isMultimodal: true,
      isReasoning: false,
      contextLength: 128000,
    });
    expect(pickedFromDiscovered(models[1], formDefaults)).toMatchObject({
      isMultimodal: false,
      isReasoning: true,
      contextLength: 8192,
    });
    expect(customPicked('my-ft', formDefaults)).toEqual({
      id: 'my-ft',
      isMultimodal: false,
      isReasoning: true,
      contextLength: 8192,
    });
  });

  it('keeps ids typed into the single-model field when picking starts', () => {
    const next = [customPicked('gpt-4o', FORM_DEFAULTS)];
    expect(seedTypedModels(next, ' my-ft ', FORM_DEFAULTS, 'My FT').map((item) => item.id)).toEqual([
      'my-ft',
      'gpt-4o',
    ]);
    expect(seedTypedModels(next, 'my-ft', FORM_DEFAULTS, 'My FT')[0].modelFriendlyName).toBe('My FT');
    expect(seedTypedModels(next, 'a, gpt-4o, b', FORM_DEFAULTS, 'Shared').map((item) => item.id)).toEqual([
      'a',
      'b',
      'gpt-4o',
    ]);
    expect(seedTypedModels(next, 'a, b', FORM_DEFAULTS, 'Shared')[0].modelFriendlyName).toBeUndefined();
    expect(seedTypedModels(next, '', FORM_DEFAULTS)).toEqual(next);
  });
});

describe('canFetchModels', () => {
  it('waits for required fields and accepts a saved credential', () => {
    expect(canFetchModels(['apiKey'], { apiKey: '' }, new Set())).toBe(false);
    expect(canFetchModels(['apiKey'], { apiKey: 'sk' }, new Set())).toBe(true);
    expect(canFetchModels(['apiKey'], {}, new Set(['apiKey']))).toBe(true);
  });
});

describe('applyBatchEvent', () => {
  it('maps progress events onto the matching row', () => {
    const rows: BatchRow[] = [
      { model: 'gpt-4o', status: 'pending' },
      { model: 'gpt-4o-mini', status: 'pending' },
    ];
    const checking = applyBatchEvent(rows, 'progress', { model: 'gpt-4o', status: 'checking' });
    const failed = applyBatchEvent(checking, 'progress', {
      model: 'gpt-4o',
      status: 'failed',
      message: 'rejected',
    });
    expect(failed[0]).toMatchObject({ status: 'failed', message: 'rejected' });
    expect(failed[1].status).toBe('pending');
    expect(applyBatchEvent(rows, 'done', {})).toEqual(rows);
  });

  it('keeps sibling rows when a retry starts over one model', () => {
    const existing: BatchRow[] = [
      { model: 'gpt-4o', status: 'healthy', modelKey: 'k1' },
      { model: 'o3', status: 'failed', message: 'rejected' },
    ];
    const merged = mergeBatchRows(existing, [{ model: 'o3', status: 'pending' }]);
    expect(merged).toEqual([
      { model: 'gpt-4o', status: 'healthy', modelKey: 'k1' },
      { model: 'o3', status: 'pending' },
    ]);
  });
});

describe('ModelPicker', () => {
  it('filters by search, hides other types, and adds a custom id once', async () => {
    const seen: PickedModel[][] = [];
    renderPicker([], (next) => seen.push(next));

    expect(screen.getByLabelText('GPT-4o')).toBeTruthy();
    expect(screen.queryByLabelText('custom-ft')).toBeNull();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Show other types' }));
    expect(screen.getByLabelText('custom-ft')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('Search models'), { target: { value: 'whisper' } });
    expect(screen.queryByLabelText('GPT-4o')).toBeNull();
    expect(screen.getByLabelText('Whisper')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('Search models'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Custom model id'), { target: { value: 'my-ft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    const added = seen.filter((rows) => rows.some((row) => row.id === 'my-ft'));
    expect(added).toHaveLength(1);
  });

  it('sets reasoning, multimodal, and context length for one model without touching the others', () => {
    const seen: PickedModel[][] = [];
    function Harness() {
      const [picked, setPicked] = React.useState<PickedModel[]>([
        customPicked('my-ft', FORM_DEFAULTS),
        customPicked('other-ft', FORM_DEFAULTS),
      ]);
      return (
        <Theme>
          <ModelPicker
            models={[]}
            picked={picked}
            onChange={(next) => {
              seen.push(next);
              setPicked(next);
            }}
            showDefault={false}
            defaultModelId={null}
            onDefaultChange={() => undefined}
            flagFields={FLAG_FIELDS}
            contextLengthLabel="Context Length"
            defaults={FORM_DEFAULTS}
          />
        </Theme>
      );
    }
    render(<Harness />);

    expect(screen.queryByPlaceholderText('Search models')).toBeNull();
    fireEvent.click(screen.getByRole('switch', { name: 'Reasoning for my-ft' }));
    expect(latest(seen, 'my-ft')?.isReasoning).toBe(true);
    expect(latest(seen, 'other-ft')?.isReasoning).toBe(false);

    fireEvent.click(screen.getByRole('switch', { name: 'Multimodal for other-ft' }));
    expect(latest(seen, 'other-ft')?.isMultimodal).toBe(true);

    fireEvent.change(screen.getByLabelText('Context Length for my-ft'), { target: { value: '32000' } });
    expect(latest(seen, 'my-ft')?.contextLength).toBe(32000);
    fireEvent.change(screen.getByLabelText('Context Length for my-ft'), { target: { value: '' } });
    expect(latest(seen, 'my-ft')?.contextLength).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Remove other-ft' }));
    expect(seen[seen.length - 1]?.map((item) => item.id)).toEqual(['my-ft']);
  });

  it('starts a picked model with the form value when the provider did not report a flag', () => {
    const seen: PickedModel[][] = [];
    renderPicker([], (next) => seen.push(next), {
      defaults: { isReasoning: true, isMultimodal: true, contextLength: null },
    });
    fireEvent.click(screen.getByLabelText('Whisper'));
    expect(seen[seen.length - 1]?.[0]).toMatchObject({ id: 'whisper-1', isReasoning: true, isMultimodal: true });
  });
});
