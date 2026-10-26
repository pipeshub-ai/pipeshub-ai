import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { hasSourceLimit, KnowledgeSourceLimitList } from '../knowledge-source-limit';
import type { FlowNodeData } from '../../types';

afterEach(cleanup);

const node = (filters: Record<string, unknown>, type = 'app-drive'): FlowNodeData =>
  ({ id: 'n1', type, label: 'Drive', config: { connectorInstanceId: 'drive-1', filters } }) as unknown as FlowNodeData;

describe('Items a knowledge source is limited to, on its node', () => {
  it('lists every limited item by its saved name, ids standing in for missing names', () => {
    render(
      <Theme>
        <KnowledgeSourceLimitList
          data={node({
            recordGroups: ['g1'],
            records: ['f1', 'r9'],
            nodes: [
              { id: 'g1', name: 'Specs space', nodeType: 'recordGroup' },
              { id: 'f1', name: 'Roadmaps', nodeType: 'folder' },
            ],
          })}
        />
      </Theme>
    );

    expect(screen.getByText('Specs space')).toBeTruthy();
    expect(screen.getByText('Roadmaps')).toBeTruthy();
    expect(screen.getByText('r9')).toBeTruthy();
  });

  it('shows nothing for a source used as a whole or for a group node', () => {
    expect(hasSourceLimit(node({ recordGroups: [], records: [] }))).toBe(false);
    expect(hasSourceLimit(node({ records: ['f1'] }, 'app-group'))).toBe(false);
    expect(hasSourceLimit(node({ records: ['f1'] }))).toBe(true);
    const { container } = render(<KnowledgeSourceLimitList data={node({})} />);
    expect(container.textContent).toBe('');
  });
});
