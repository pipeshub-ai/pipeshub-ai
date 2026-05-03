'use client';

import { Box, Flex, Text } from '@radix-ui/themes';

interface MediaRendererProps {
  fileUrl: string;
  fileName: string;
  fileType: string; // MIME type
}

export function MediaRenderer({ fileUrl, fileName: _fileName, fileType }: MediaRendererProps) {
  const isVideo = fileType.startsWith('video/');
  const _isAudio = fileType.startsWith('audio/');

  if (!fileUrl || fileUrl.trim() === '') {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--slate-9)' }}>
          {isVideo ? 'movie' : 'audio_file'}
        </span>
        <Text size="3" weight="medium" color="gray">
          {isVideo ? 'Video' : 'Audio'} file URL not available
        </Text>
      </Flex>
    );
  }

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        width: '100%',
        height: '100%',
        padding: 'var(--space-4)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <Box
        style={{
          width: '100%',
          maxWidth: isVideo ? '100%' : '37.5rem',
          backgroundColor: 'var(--slate-12)',
          borderRadius: 'var(--radius-3)',
          overflow: 'hidden',
          boxShadow: '0px 12px 32px -16px rgba(0, 0, 51, 0.06), 0px 8px 40px 0px rgba(0, 0, 0, 0.05)',
        }}
      >
        {isVideo ? (
          <video
            src={fileUrl}
            controls
            style={{
              width: '100%',
              height: 'auto',
              display: 'block',
              backgroundColor: 'var(--slate-12)',
            }}
          >
            Your browser does not support video playback.
          </video>
        ) : (
          <audio
            src={fileUrl}
            controls
            style={{
              width: '100%',
              height: '60px',
              backgroundColor: 'var(--slate-12)',
            }}
          >
            Your browser does not support audio playback.
          </audio>
        )}
      </Box>
    </Flex>
  );
}
