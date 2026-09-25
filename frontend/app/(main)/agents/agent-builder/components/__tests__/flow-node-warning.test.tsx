import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { FlowNodeData } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('../agent-core-node', () => ({ AgentCoreNode: () => null }));
vi.mock('../toolset-flow-node', () => ({ ToolsetFlowNode: () => null }));
vi.mock('../mcp-flow-node', () => ({ McpFlowNode: () => null }));
vi.mock('../node-handles', () => ({ NodeHandles: () => null }));

vi.mock('@/app/components/ui', () => ({
  ConnectorIcon: () => null,
}));

vi.mock('@/app/components/ui/themeable-asset-icon', () => ({
  ThemeableAssetIcon: () => null,
  themeableAssetIconPresets: { flowNodeHeader: {}, flowNodeWell: {} },
}));

import { FlowNode } from '../flow-node';

afterEach(() => cleanup());

function skillData(overrides: Partial<FlowNodeData> = {}): FlowNodeData {
  return {
    id: 'skill-1',
    type: 'skill-pdf-extractor',
    label: 'pdf-extractor',
    description: 'Extracts tables',
    icon: 'psychology',
    category: 'skills',
    config: { skillName: 'pdf-extractor' },
    inputs: [],
    outputs: ['output'],
    isConfigured: true,
    ...overrides,
  };
}

function renderNode(data: FlowNodeData) {
  return render(
    <Theme>
      <FlowNode id="skill-1" data={data} selected={false} />
    </Theme>,
  );
}

describe('FlowNode skill warning', () => {
  it('renders a warning glyph when data.warning is set', () => {
    renderNode(skillData({ warning: 'This skill has been deprecated' }));
    expect(screen.getByTestId('flow-node-warning')).toBeTruthy();
  });

  it('omits the warning glyph when the skill is current', () => {
    renderNode(skillData());
    expect(screen.queryByTestId('flow-node-warning')).toBeNull();
  });
});
