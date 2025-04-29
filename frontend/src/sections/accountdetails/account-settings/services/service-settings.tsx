import { useState } from 'react';

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
      {value === index && <Box sx={{ p: 2 }}>{children}</Box>}
    </div>
  );
}

export default function ServiceSettings() {
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
        sx={{ height: 300 }}
        width="100%"
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%' }}>
      <Box sx={{ borderBottom: 1, borderColor: 'divider', pl: 2 }}>
        <Tabs value={tabValue} onChange={handleTabChange} aria-label="services tabs">
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
