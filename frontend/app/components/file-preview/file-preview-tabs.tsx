'use client';

import { SegmentedControl } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { FilePreviewTab, TabConfig } from './types';

interface FilePreviewTabsProps {
  tabs: TabConfig[];
  activeTab: FilePreviewTab;
  onTabChange: (tab: FilePreviewTab) => void;
}

export function FilePreviewTabs({ tabs, activeTab, onTabChange }: FilePreviewTabsProps) {
  const { t } = useTranslation();
  const visibleTabs = tabs.filter(tab => tab.visible);
  const labels: Record<FilePreviewTab, string> = {
    preview: t('filePreview.preview'),
    'file-details': t('filePreview.fileDetails'),
  };
  
  return (
    <SegmentedControl.Root 
      value={activeTab} 
      onValueChange={(value) => onTabChange(value as FilePreviewTab)}
      style={{ width: '100%' }}
    >
      {visibleTabs.map((tab) => (
        <SegmentedControl.Item key={tab.id} value={tab.id}>
          {labels[tab.id]}
        </SegmentedControl.Item>
      ))}
    </SegmentedControl.Root>
  );
}
