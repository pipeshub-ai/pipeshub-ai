import type { BoxProps } from '@mui/material/Box';
import type { Breakpoint } from '@mui/material/styles';

import Box from '@mui/material/Box';
import { useTheme } from '@mui/material/styles';

import { layoutClasses } from 'src/layouts/classes';

// ----------------------------------------------------------------------

type MainProps = BoxProps & {
  layoutQuery: Breakpoint;
};

export function Main({ sx, children, layoutQuery, ...other }: MainProps) {
  const gridColumns: Record<string, string> = { xs: '1fr' };
  gridColumns[layoutQuery] = 'repeat(2, minmax(0, 1fr))';

  return (
    <Box
      component="main"
      className={layoutClasses.main}
      sx={{
        height: '100vh',
        maxHeight: '100vh',
        width: 1,
        display: 'flex',
        flex: '1 1 auto',
        flexDirection: 'column',
        boxSizing: 'border-box',
        px: { xs: 2, md: 2 },
        py: { xs: 3, md: 2 },
        backgroundColor: 'background.default',
        overflow: 'hidden',
        ...sx,
      }}
      {...other}
    >
      <Box
        sx={{
          flex: 1,
          display: 'grid',
          gap: { xs: 2, md: 2 },
          alignItems: 'stretch',
          gridTemplateColumns: gridColumns,
          width: 1,
          height: 1,
          minHeight: 0,
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

// ----------------------------------------------------------------------

export function Content({ sx, children, layoutQuery, ...other }: MainProps) {
  const theme = useTheme();

  const renderContent = (
    <Box
      sx={{
        width: 1,
        display: 'flex',
        flexDirection: 'column',
        height: 1,
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      {children}
    </Box>
  );

  return (
    <Box
      className={layoutClasses.content}
      sx={{
        position: 'relative',
        display: 'flex',
        flex: '1 1 auto',
        flexDirection: 'column',
        justifyContent: 'flex-start',
        alignItems: 'stretch',
        height: 1,
        minHeight: 0,
        maxHeight: '100%',
        overflow: 'hidden',
        boxSizing: 'border-box',
        padding: theme.spacing(2),
        [theme.breakpoints.up(layoutQuery)]: {
          justifyContent: 'center',
          alignItems: 'stretch',
          padding: theme.spacing(2),
        },
        ...sx,
      }}
      {...other}
    >
      {renderContent}
    </Box>
  );
}
