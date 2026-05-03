'use client';

import { Box, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

const ICON_SIZE = 18;

interface ZoomActionBarProps {
  zoomLevel: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitScreen: () => void;
  minZoom: number;
  maxZoom: number;
}

export function ZoomActionBar({
  zoomLevel,
  onZoomIn,
  onZoomOut,
  onFitScreen,
  minZoom,
  maxZoom,
}: ZoomActionBarProps) {
  const percentage = Math.round(zoomLevel * 100);
  const isAtMin = zoomLevel <= minZoom;
  const isAtMax = zoomLevel >= maxZoom;

  return (
    <Flex
      direction="column"
      align="center"
      justify="end"
      gap="5"
      style={{
        height: '100%',
        backgroundColor: 'var(--gray-2)',
        padding: 'var(--space-2)',
        paddingBottom: 'var(--space-6)',
        userSelect: 'none',
      }}
    >
      <Tooltip content="Zoom in" side="left">
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onZoomIn}
          disabled={isAtMax}
          aria-label="Zoom in"
          style={{ width: 32, height: 32, padding: 0 }}
        >
          <MaterialIcon name="zoom_in" size={ICON_SIZE} />
        </IconButton>
      </Tooltip>

      <Tooltip content="Zoom out" side="left">
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onZoomOut}
          disabled={isAtMin}
          aria-label="Zoom out"
          style={{ width: 32, height: 32, padding: 0 }}
        >
          <MaterialIcon name="zoom_out" size={ICON_SIZE} />
        </IconButton>
      </Tooltip>

      <Tooltip content="Fit to screen" side="left">
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onFitScreen}
          aria-label="Fit to screen"
          style={{ width: 32, height: 32, padding: 0 }}
        >
          <MaterialIcon name="fit_screen" size={ICON_SIZE} />
        </IconButton>
      </Tooltip>

      <Box
        style={{
          width: '100%',
          borderTop: '1px solid var(--olive-5)',
          paddingTop: 'var(--space-1)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: 32,
        }}
      >
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--gray-11)',
            fontSize: '11px',
            lineHeight: 1,
            letterSpacing: '0.02em',
          }}
        >
          {percentage}%
        </Text>
      </Box>
    </Flex>
  );
}
