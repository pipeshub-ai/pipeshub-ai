import type { StackProps } from '@mui/material/Stack';
import type { Theme, SxProps } from '@mui/material/styles';

import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

import { varAlpha } from 'src/theme/styles';

// ----------------------------------------------------------------------

export type EmptyContentProps = StackProps & {
  title?: string;
  imgUrl?: string;
  filled?: boolean;
  description?: string;
  action?: React.ReactNode;
  slotProps?: {
    img?: SxProps<Theme>;
    title?: SxProps<Theme>;
    description?: SxProps<Theme>;
  };
};

export function EmptyContent({
  sx,
  imgUrl,
  action,
  filled,
  slotProps,
  description,
  title = 'No data',
  ...other
}: EmptyContentProps) {
  return (
    <Stack
      flexGrow={1}
      alignItems="center"
      justifyContent="center"
      sx={{
        px: 3,
        height: 1,
        ...(filled && {
          borderRadius: 2,
          bgcolor: (theme) => varAlpha(theme.vars.palette.grey['500Channel'], 0.04),
          border: (theme) => `dashed 1px ${varAlpha(theme.vars.palette.grey['500Channel'], 0.08)}`,
        }),
        ...sx,
      }}
      {...other}
    >
      <Box
        component="img"
        alt="empty content"
        sx={{ width: 1, maxWidth: 160, ...slotProps?.img }}
      />

      {title && (
        <Typography
          variant="h6"
          component="span"
          sx={{ mt: 1, textAlign: 'center', ...slotProps?.title, color: 'text.disabled' }}
        >
          {title}
        </Typography>
      )}

      {description && (
        <Typography
          variant="caption"
          sx={{ mt: 1, textAlign: 'center', color: 'text.disabled', ...slotProps?.description }}
        >
          {description}
        </Typography>
      )}

      {action && action}
    </Stack>
  );
}
