'use client';

import { useTranslation } from 'react-i18next';
import { SegmentedTabs } from '@/app/components/ui/segmented-tabs';
import type { FilePreviewTab, TabConfig } from './types';

interface FilePreviewTabsProps {
  tabs: TabConfig[];
  activeTab: FilePreviewTab;
  onTabChange: (tab: FilePreviewTab) => void;
}

export function FilePreviewTabs({ tabs, activeTab, onTabChange }: FilePreviewTabsProps) {
  const { t } = useTranslation();
  const labels: Record<FilePreviewTab, string> = {
    preview: t('filePreview.preview'),
    'file-details': t('filePreview.fileDetails'),
  };

  return (
    <SegmentedTabs
      value={activeTab}
      onValueChange={onTabChange}
      items={tabs.filter((tab) => tab.visible).map((tab) => ({ value: tab.id, label: labels[tab.id] }))}
    />
  );
}
