import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import {
  PickedModelsList,
  customPicked,
  pickedFromDiscovered,
  type PerModelFlagField,
  type PickedDefaults,
} from '../components/model-picker';
import { ModelCombobox, buildModelOptions } from '../components/model-combobox';
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

describe('picked model flags', () => {
  it('prefills at most 300K tokens of context even when the model accepts more', () => {
    const big = { id: 'gpt-6-luna', displayName: 'gpt-6-luna', capabilities: ['llm'], contextLength: 922000 };
    expect(pickedFromDiscovered(big, FORM_DEFAULTS).contextLength).toBe(300000);
    expect(pickedFromDiscovered({ ...big, contextLength: 128000 }, FORM_DEFAULTS).contextLength).toBe(128000);
  });

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

describe('buildModelOptions', () => {
  const listed: DiscoveredModel[] = [
    { id: 'old-model', displayName: 'old-model', capabilities: ['llm'], deprecated: true },
    { id: 'custom-ft', displayName: 'custom-ft', capabilities: ['other'] },
    { id: 'gpt-4o', displayName: 'GPT-4o', capabilities: ['llm'] },
    { id: 'GPT-4O', displayName: 'GPT-4O', capabilities: ['llm'] },
  ];

  it('lists current models first, deprecated next, and unclassified last, once each', () => {
    expect(buildModelOptions(listed, '', new Set()).map((option) => option.id)).toEqual([
      'gpt-4o',
      'old-model',
      'custom-ft',
    ]);
  });

  it('puts the id that was typed first, ahead of newer ids that contain it', () => {
    const newestFirst: DiscoveredModel[] = [
      { id: 'gpt-4o-mini', displayName: 'gpt-4o-mini', capabilities: ['llm'] },
      { id: 'GPT-4o', displayName: 'GPT-4o', capabilities: ['llm'] },
    ];
    expect(buildModelOptions(newestFirst, 'gpt-4o', new Set()).map((option) => option.id)).toEqual([
      'GPT-4o',
      'gpt-4o-mini',
    ]);
  });

  it('matches every word of a search in any order, and offers the phrase itself last', () => {
    const hub: DiscoveredModel[] = [
      { id: 'intfloat/multilingual-e5-small', displayName: 'intfloat/multilingual-e5-small', capabilities: ['embedding'] },
      { id: 'efederici/e5-base-multilingual-4096', displayName: 'efederici/e5-base-multilingual-4096', capabilities: ['embedding'] },
      { id: 'BAAI/bge-small-en-v1.5', displayName: 'BAAI/bge-small-en-v1.5', capabilities: ['embedding'] },
    ];
    expect(buildModelOptions(hub, 'multilingual e5', new Set()).map((option) => option.id)).toEqual([
      'intfloat/multilingual-e5-small',
      'efederici/e5-base-multilingual-4096',
      'multilingual e5',
    ]);
  });

  it('offers the typed text as a model only when nothing matches it exactly', () => {
    expect(buildModelOptions(listed, 'gpt', new Set())[0]).toEqual({ kind: 'custom', id: 'gpt' });
    expect(buildModelOptions(listed, 'gpt-4o', new Set()).some((option) => option.kind === 'custom')).toBe(false);
    expect(buildModelOptions([], 'my-ft', new Set(['my-ft']))).toEqual([]);
  });
});

function renderCombobox(multiple: boolean, initial: string[] = []) {
  const seen: string[][] = [];
  function Harness() {
    const [value, setValue] = React.useState<string[]>(initial);
    return (
      <Theme>
        <ModelCombobox
          label="Model Name"
          models={models}
          value={value}
          multiple={multiple}
          onChange={(next) => {
            seen.push(next);
            setValue(next);
          }}
        />
      </Theme>
    );
  }
  render(<Harness />);
  return { seen, input: screen.getByRole('combobox', { name: 'Model Name' }) };
}

describe('ModelCombobox', () => {
  it('searches the list, picks several models, and accepts a typed id', () => {
    const { seen, input } = renderCombobox(true);

    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'gpt' } });
    expect(screen.queryByRole('option', { name: /Whisper/ })).toBeNull();
    fireEvent.click(screen.getByRole('option', { name: /GPT-4o/ }));

    fireEvent.change(input, { target: { value: 'my-ft' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(seen[seen.length - 1]).toEqual(['gpt-4o', 'my-ft']);
    expect(screen.getByRole('button', { name: 'Remove gpt-4o' })).toBeTruthy();

    fireEvent.keyDown(input, { key: 'Backspace' });
    expect(seen[seen.length - 1]).toEqual(['gpt-4o']);
  });

  it('closes only the list on Escape, not the surrounding drawer', () => {
    const onWindowEscape = vi.fn();
    window.addEventListener('keydown', onWindowEscape);
    const { input } = renderCombobox(true);
    fireEvent.focus(input);
    expect(screen.getByRole('listbox')).toBeTruthy();

    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onWindowEscape).not.toHaveBeenCalled();
    window.removeEventListener('keydown', onWindowEscape);
  });

  it('removing a chip while typing leaves the half-typed id in the input', () => {
    const seen: string[][] = [];
    function Harness() {
      const [value, setValue] = React.useState<string[]>(['a']);
      return (
        <Theme>
          <ModelCombobox
            label="Model Name"
            models={[]}
            value={value}
            multiple
            onChange={(next) => {
              seen.push(next);
              setValue(next);
            }}
          />
        </Theme>
      );
    }
    render(<Harness />);
    const input = screen.getByRole('combobox', { name: 'Model Name' });
    input.focus();
    fireEvent.change(input, { target: { value: 'b' } });

    const removeA = screen.getByRole('button', { name: 'Remove a' });
    // A browser moves focus on mousedown unless the handler prevents it.
    if (fireEvent.mouseDown(removeA)) fireEvent.blur(input);
    fireEvent.click(removeA);

    expect(seen[seen.length - 1]).toEqual([]);
    expect((input as HTMLInputElement).value).toBe('b');
  });

  it('Enter picks the typed id when a newer listed id contains it', () => {
    const seen: string[][] = [];
    function Harness() {
      const [value, setValue] = React.useState<string[]>([]);
      return (
        <Theme>
          <ModelCombobox
            label="Model Name"
            models={[
              { id: 'gpt-4o-mini', displayName: 'gpt-4o-mini', capabilities: ['llm'] },
              { id: 'gpt-4o', displayName: 'gpt-4o', capabilities: ['llm'] },
            ]}
            value={value}
            multiple
            onChange={(next) => {
              seen.push(next);
              setValue(next);
            }}
          />
        </Theme>
      );
    }
    render(<Harness />);
    const input = screen.getByRole('combobox', { name: 'Model Name' });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'gpt-4o' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(seen[seen.length - 1]).toEqual(['gpt-4o']);
  });

  it('Enter on a multi-word search picks the top match, not the phrase', () => {
    const seen: string[][] = [];
    function Harness() {
      const [value, setValue] = React.useState<string[]>([]);
      return (
        <Theme>
          <ModelCombobox
            label="Model Name"
            models={[
              { id: 'intfloat/multilingual-e5-small', displayName: 'intfloat/multilingual-e5-small', capabilities: ['embedding'] },
            ]}
            value={value}
            multiple
            onChange={(next) => {
              seen.push(next);
              setValue(next);
            }}
          />
        </Theme>
      );
    }
    render(<Harness />);
    const input = screen.getByRole('combobox', { name: 'Model Name' });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'multilingual e5' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(seen[seen.length - 1]).toEqual(['intfloat/multilingual-e5-small']);
  });

  it('splits a pasted comma list into one model each', () => {
    const { seen, input } = renderCombobox(true);
    fireEvent.change(input, { target: { value: 'a, b, a' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(seen[seen.length - 1]).toEqual(['a', 'b']);
  });

  it('replaces the model when only one is allowed', () => {
    const { seen, input } = renderCombobox(false, ['gpt-4o']);
    fireEvent.focus(input);
    fireEvent.click(screen.getByRole('option', { name: /Whisper/ }));
    expect(seen[seen.length - 1]).toEqual(['whisper-1']);
  });
});

describe('PickedModelsList', () => {
  it('sets reasoning, multimodal, and context length for one model without touching the others', () => {
    const seen: PickedModel[][] = [];
    function Harness() {
      const [picked, setPicked] = React.useState<PickedModel[]>([
        customPicked('my-ft', FORM_DEFAULTS),
        customPicked('other-ft', FORM_DEFAULTS),
      ]);
      return (
        <Theme>
          <PickedModelsList
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
          />
        </Theme>
      );
    }
    render(<Harness />);

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
});
