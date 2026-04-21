'use client';

import React, { useCallback } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { apiClient } from '@/lib/api';
import { isSignedUrl } from '../../utils/parse-download-markers';

interface DownloadTask {
  fileName: string;
  url: string;
}

interface DownloadTasksProps {
  tasks: DownloadTask[];
}

export function DownloadTasks({ tasks }: DownloadTasksProps) {
  const handleDownload = useCallback(async (task: DownloadTask) => {
    try {
      if (isSignedUrl(task.url)) {
        // S3 / Azure presigned — direct click, no bearer token.
        const a = document.createElement('a');
        a.href = task.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.download = task.fileName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        return;
      }

      // Local / internal API — axios instance attaches the auth token.
      const response = await apiClient.get(task.url, { responseType: 'blob' });
      const objectUrl = window.URL.createObjectURL(response.data);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = task.fileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(objectUrl);
    } catch (e) {
      console.error('Download failed:', e);
    }
  }, []);

  if (tasks.length === 0) return null;

  return (
    <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-3)' }}>
      <Text size="1" color="gray">
        You can download the complete query results:
      </Text>
      <Flex gap="2" wrap="wrap">
        {tasks.map((task, idx) => (
          <Button
            key={`${task.fileName}-${idx}`}
            variant="outline"
            size="1"
            color="gray"
            onClick={() => handleDownload(task)}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="download" size={ICON_SIZES.SECONDARY} />
            {task.fileName}
          </Button>
        ))}
      </Flex>
    </Flex>
  );
}
