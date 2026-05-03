'use client';

import { SegmentedControl } from '@radix-ui/themes';
import type { FilePreviewTab, TabConfig } from './types';

interface FilePreviewTabsProps {
  tabs: TabConfig[];
  activeTab: FilePreviewTab;
  onTabChange: (tab: FilePreviewTab) => void;
}

export function FilePreviewTabs({ tabs, activeTab, onTabChange }: FilePreviewTabsProps) {
  const visibleTabs = tabs.filter(tab => tab.visible);
  
  return (
    <SegmentedControl.Root 
      value={activeTab} 
      onValueChange={(value) => onTabChange(value as FilePreviewTab)}
      style={{ width: '100%' }}
    >
      {visibleTabs.map((tab) => (
        <SegmentedControl.Item key={tab.id} value={tab.id}>
          {tab.label}
        </SegmentedControl.Item>
      ))}
    </SegmentedControl.Root>
  );
}
