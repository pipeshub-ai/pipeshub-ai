import { useState } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import { Box, Tab, Tabs, CircularProgress } from '@mui/material';

import ExternalServicesSettings from './external-services-settings';
import InternalServicesSettings from './internal-services-settings';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`services-tabpanel-${index}`}
      aria-labelledby={`services-tab-${index}`}
      {...other}
    >
      {value === index && <Box>{children}</Box>}
    </div>
  );
}

export default function ServiceSettings() {
  const theme = useTheme();
  const [tabValue, setTabValue] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(false);

  const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
  };

  if (loading) {
    return (
      <Box
        display="flex"
        justifyContent="center"
        alignItems="center"
        sx={{
          height: 200,
          width: '100%',
          my: 4,
        }}
      >
        <CircularProgress size={28} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        width: '100%',
        bgcolor:
          theme.palette.mode === 'dark'
            ? alpha(theme.palette.background.paper, 0.4)
            : theme.palette.background.paper,
        borderRadius: 1,
        overflow: 'hidden',
        boxShadow:
          theme.palette.mode === 'dark'
            ? `0 1px 3px ${alpha('#000', 0.12)}`
            : `0 1px 3px ${alpha(theme.palette.grey[500], 0.08)}`,
      }}
    >
      <Box
        sx={{
          borderBottom: 1,
          borderColor: 'divider',
          px: { xs: 1, sm: 2 },
        }}
      >
        <Tabs
          value={tabValue}
          onChange={handleTabChange}
          aria-label="services tabs"
          sx={{
            minHeight: 48,
            '& .MuiTabs-indicator': {
              height: 3,
              borderTopLeftRadius: 3,
              borderTopRightRadius: 3,
            },
            '& .MuiTab-root': {
              textTransform: 'none',
              fontWeight: 500,
              fontSize: '0.875rem',
              minHeight: 48,
              py: 1,
              px: { xs: 1, sm: 2 },
              '&.Mui-selected': {
                fontWeight: 600,
                color: theme.palette.primary.main,
              },
            },
          }}
        >
          <Tab label="Internal Services" id="services-tab-0" aria-controls="services-tabpanel-0" />
          <Tab label="External Services" id="services-tab-1" aria-controls="services-tabpanel-1" />
        </Tabs>
      </Box>
      <TabPanel value={tabValue} index={0}>
        <InternalServicesSettings />
      </TabPanel>
      <TabPanel value={tabValue} index={1}>
        <ExternalServicesSettings />
      </TabPanel>
    </Box>
  );
}
