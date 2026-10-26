import React, { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ChatKnowledgeFilters } from '@/chat/types';
import { KnowledgeLimitChoice } from '../knowledge-limit-choice';
import type { KnowledgeLimitValue } from '../knowledge-limit';

const picked: ChatKnowledgeFilters = { apps: ['app-1'], kb: [], recordGroups: [], records: [] };

vi.mock('@/chat/components/chat-panel/expansion-panels/connectors-collections', () => ({
  CollectionsTab: ({ onSelectionChange }: { onSelectionChange: (next: ChatKnowledgeFilters) => void }) => (
    <button type="button" onClick={() => onSelectionChange(picked)}>pick</button>
  ),
}));

afterEach(cleanup);

function Harness({ onChange }: { onChange: (next: KnowledgeLimitValue) => void }) {
  const [value, setValue] = useState<KnowledgeLimitValue>(null);
  return (
    <Theme>
      <KnowledgeLimitChoice
        appName="App"
        idPrefix="t"
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
    </Theme>
  );
}

describe('Choosing what a connected app can see', () => {
  it('keeps the picked items when the user switches to everything and back', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const only = document.getElementById('t-knowledge-limit-only')!;
    const everything = document.getElementById('t-knowledge-limit-everything')!;

    fireEvent.click(only);
    fireEvent.click(screen.getByText('pick'));
    fireEvent.click(everything);
    fireEvent.click(only);

    expect(onChange.mock.calls.map(([next]) => next)).toEqual([
      { apps: [], kb: [], recordGroups: [], records: [] },
      picked,
      null,
      picked,
    ]);
  });
});
