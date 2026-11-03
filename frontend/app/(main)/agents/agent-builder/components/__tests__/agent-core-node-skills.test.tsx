import React from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { FlowNodeData } from '../../types';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

const flow = vi.hoisted(() => ({
  nodes: [] as { id: string; data: unknown }[],
  edges: [] as { source: string; target: string; targetHandle?: string }[],
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useReactFlow: () => ({ setNodes: vi.fn() }),
  useStore: (selector: (s: typeof flow) => unknown) => selector(flow),
  useNodeConnections: () => [],
}));

vi.mock('@/app/components/ui', () => ({
  ConnectorIcon: () => null,
}));

vi.mock('@/app/components/ui/themeable-asset-icon', () => ({
  ThemeableAssetIcon: () => null,
  themeableAssetIconPresets: { flowNodeHeader: {}, flowNodeWell: {} },
}));

import { AgentCoreNode } from '../agent-core-node';

const AGENT: FlowNodeData = {
  id: 'agent-core',
  type: 'agent-core',
  label: 'Agent',
  description: '',
  icon: 'smart_toy',
  category: 'agent',
  config: {},
  inputs: [],
  outputs: [],
  isConfigured: true,
};

const SKILL: FlowNodeData = {
  id: 'skill-1',
  type: 'skill-pdf-extractor',
  label: 'pdf-extractor',
  description: '',
  icon: 'psychology',
  category: 'skills',
  config: { skillName: 'pdf-extractor' },
  inputs: [],
  outputs: ['output'],
  isConfigured: true,
};

function renderCore() {
  return render(
    <Theme>
      <AgentCoreNode data={AGENT} selected={false} />
    </Theme>,
  );
}

describe('AgentCoreNode skills section', () => {
  beforeEach(() => {
    flow.nodes = [];
    flow.edges = [];
  });

  afterEach(() => {
    cleanup();
    useFeatureFlagsStore.setState({ flags: null });
  });

  it('is hidden when skills are turned off and none are connected', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: false } });
    renderCore();
    expect(screen.queryByText('agentBuilder.skillsSection')).toBeNull();
  });

  it('stays for an agent that already has a skill connected', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: false } });
    flow.nodes = [{ id: SKILL.id, data: SKILL }];
    flow.edges = [{ source: SKILL.id, target: AGENT.id, targetHandle: 'skills' }];
    renderCore();
    expect(screen.queryByText('agentBuilder.skillsSection')).not.toBeNull();
  });

  it('is shown when skills are on', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_SKILLS: true } });
    renderCore();
    expect(screen.queryByText('agentBuilder.skillsSection')).not.toBeNull();
  });
});
