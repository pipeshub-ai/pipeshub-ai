import { useState } from 'react';
import { Icon } from '@iconify/react';
import redisIcon from '@iconify-icons/logos/redis';
import storageIcon from '@iconify-icons/mdi/storage';
import kafkaIcon from '@iconify-icons/mdi/apache-kafka';
import qdrantIcon from '@iconify-icons/logos/qdrant-icon';
import mongodbIcon from '@iconify-icons/logos/mongodb-icon';
import arangodbIcon from '@iconify-icons/logos/arangodb-icon';
import settingsIcon from '@iconify-icons/mdi/settings-outline';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Paper,
  Alert,
  Snackbar,
  Container,
  Typography,
  IconButton,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import ConfigureServiceDialog from './configure-services-dialog';

// Service configuration list
const SERVICES_LIST = [
  {
    id: 'redis',
    icon: redisIcon,
    title: 'Redis',
    description: 'In-memory data structure store used as a database, cache, and message broker',
    color: '#DC382D',
    darkModeColor: '#DC382D', // Same color for dark mode
  },
  {
    id: 'kafka',
    icon: kafkaIcon,
    description: 'Distributed event streaming platform for high-performance data pipelines',
    title: 'Kafka',
    color: '#00000',
    darkModeColor: '#BFB0BF', // Light gray for dark mode
  },
  {
    id: 'mongoDb',
    icon: mongodbIcon,
    description: 'NoSQL document database for modern applications',
    title: 'MongoDB',
    color: '#47A248',
    darkModeColor: '#47A048', // Same color for dark mode
  },
  {
    id: 'arangoDb',
    icon: arangodbIcon,
    description: 'Multi-model database system for graphs, documents and key-values',
    title: 'ArangoDB',
    color: '#D12C2F',
    darkModeColor: '#D12C2F', // Same color for dark mode
  },
  {
    id: 'qdrant',
    icon: qdrantIcon,
    description: 'Vector database for similarity search and machine learning',
    title: 'Qdrant',
    color: '#FF9800',
    darkModeColor: '#FF9800', // Same color for dark mode
  },
  {
    id: 'storage',
    icon: storageIcon,
    description: 'Configure storage options for your application (Local, S3, Azure Blob)',
    title: 'Storage Service',
    color: '#0078D4',
    darkModeColor: '#0078D4', // Same color for dark mode
  },
];

// Define the save result interface
interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

const ExternalServicesSettings = () => {
  const theme = useTheme();
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [currentService, setCurrentService] = useState<string | null>(null);

  // Notification states
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState(
    'External services settings updated successfully'
  );

  // Handle closing of different snackbars
  const handleCloseSuccess = () => {
    setSuccess(false);
  };

  const handleCloseError = () => {
    setError(null);
  };

  const handleCloseWarning = () => {
    setWarning(null);
  };

  // Handle service selection
  const handleConfigureService = (serviceId: string) => {
    setCurrentService(serviceId);
    setConfigDialogOpen(true);
  };

  const handleSaveConfiguration = (result?: SaveResult) => {
    if (result) {
      if (result.warning) {
        setWarning(result.warning);
      }

      if (result.error) {
        setError(result.error);
        // Keep dialog open if there's an error
        return;
      }

      if (result.success) {
        // Only show success if there was no error
        setSuccessMessage(
          `${currentService ? getServiceTitle(currentService) : 'Service'} configuration updated successfully`
        );
        setSuccess(true);
      }
    } else {
      // Legacy support - if no result object is provided, assume success
      setSuccessMessage(
        `${currentService ? getServiceTitle(currentService) : 'Service'} configuration updated successfully`
      );
      setSuccess(true);
    }

    setConfigDialogOpen(false);
    setCurrentService(null);
  };

  // Helper to get service title for success message
  const getServiceTitle = (serviceId: string): string => {
    const service = SERVICES_LIST.find((s) => s.id === serviceId);
    return service ? service.title : 'Service';
  };

  // Helper function to get the appropriate color based on theme mode
  const getServiceColor = (service: (typeof SERVICES_LIST)[0]) =>
    theme.palette.mode === 'dark' && service.darkModeColor ? service.darkModeColor : service.color;

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: theme.palette.divider,
          backgroundColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : theme.palette.background.paper,
        }}
      >
        {/* Header section */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 3,
            gap: 2,
          }}
        >
          <Box>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 0.5,
                fontSize: '1.25rem',
                color: theme.palette.text.primary,
              }}
            >
              External Services
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: 500,
                lineHeight: 1.5,
              }}
            >
              Configure connections to external services and databases
            </Typography>
          </Box>
        </Box>

        {/* Services Grid */}
        <Grid container spacing={2} mb={3}>
          {SERVICES_LIST.map((service) => {
            const serviceColor = getServiceColor(service);
            const needsBorder = theme.palette.mode === 'dark' && service.id === 'kafka';

            return (
              <Grid item xs={12} key={service.id}>
                <Paper
                  elevation={0}
                  sx={{
                    p: 2,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    borderRadius: 1,
                    border: '1px solid',
                    borderColor: theme.palette.divider,
                    bgcolor: 'transparent',
                    transition: 'all 0.15s ease-in-out',
                    '&:hover': {
                      transform: 'translateY(-2px)',
                      boxShadow:
                        theme.palette.mode === 'dark'
                          ? `0 4px 12px ${alpha('#000', 0.15)}`
                          : `0 4px 12px ${alpha(theme.palette.grey[500], 0.1)}`,
                      borderColor: alpha(serviceColor, theme.palette.mode === 'dark' ? 0.4 : 0.4),
                    },
                  }}
                >
                  {/* Service info */}
                  <Box sx={{ display: 'flex', alignItems: 'center', flexGrow: 1 }}>
                    <Box
                      sx={{
                        width: 40,
                        height: 40,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mr: 2,
                        bgcolor: needsBorder
                          ? alpha('#000', 0.5) // Darker background for Kafka in dark mode
                          : alpha(serviceColor, theme.palette.mode === 'dark' ? 0.2 : 0.1),
                        color: serviceColor,
                        borderRadius: 1,
                        flexShrink: 0,
                        border: needsBorder ? `1px solid ${alpha(serviceColor, 0.6)}` : 'none',
                      }}
                    >
                      <Icon icon={service.icon} width={22} height={22} />
                    </Box>

                    <Box>
                      <Typography
                        variant="subtitle1"
                        sx={{
                          fontWeight: 600,
                          fontSize: '0.9375rem',
                        }}
                      >
                        {service.title}
                      </Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{
                          fontSize: '0.8125rem',
                          lineHeight: 1.5,
                        }}
                      >
                        {service.description}
                      </Typography>
                    </Box>
                  </Box>

                  {/* Settings icon */}
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleConfigureService(service.id);
                    }}
                    sx={{
                      p: 0.75,
                      color: theme.palette.text.secondary,
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.background.paper, 0.3)
                          : alpha(theme.palette.background.default, 0.8),
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                      '&:hover': {
                        bgcolor: alpha(
                          theme.palette.primary.main,
                          theme.palette.mode === 'dark' ? 0.15 : 0.08
                        ),
                        color: theme.palette.primary.main,
                      },
                    }}
                    aria-label={`Configure ${service.title}`}
                  >
                    <Iconify icon={settingsIcon} width={18} height={18} />
                  </IconButton>
                </Paper>
              </Grid>
            );
          })}
        </Grid>
      </Paper>

      {/* Configure Service Dialog */}
      <ConfigureServiceDialog
        open={configDialogOpen}
        onClose={() => setConfigDialogOpen(false)}
        onSave={handleSaveConfiguration}
        serviceType={currentService}
      />

      {/* Success snackbar */}
      <Snackbar
        open={success}
        autoHideDuration={4000}
        onClose={handleCloseSuccess}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSuccess}
          severity="success"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow:
              theme.palette.mode === 'dark'
                ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
                : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {successMessage}
        </Alert>
      </Snackbar>

      {/* Warning snackbar */}
      <Snackbar
        open={!!warning}
        autoHideDuration={4000}
        onClose={handleCloseWarning}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 12 }}
      >
        <Alert
          onClose={handleCloseWarning}
          severity="warning"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow:
              theme.palette.mode === 'dark'
                ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
                : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {warning}
        </Alert>
      </Snackbar>

      {/* Error snackbar */}
      <Snackbar
        open={!!error}
        autoHideDuration={4000}
        onClose={handleCloseError}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 18 }}
      >
        <Alert
          onClose={handleCloseError}
          severity="error"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow:
              theme.palette.mode === 'dark'
                ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
                : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {error}
        </Alert>
      </Snackbar>

      <Alert
        variant="outlined"
        severity="info"
        sx={{
          mt: 3,
          mb: 1,
          borderRadius: 1,
          borderColor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.3 : 0.2),
          '& .MuiAlert-icon': {
            color: theme.palette.info.main,
          },
        }}
      >
        <Typography variant="body2">
          Refer to{' '}
          <Link
            href="https://docs.pipeshub.com/system-overview/external-services"
            target="_blank"
            rel="noopener"
            sx={{
              color: theme.palette.primary.main,
              textDecoration: 'none',
              fontWeight: 500,
              '&:hover': {
                textDecoration: 'underline',
              },
            }}
          >
            the documentation
          </Link>{' '}
          for more information.
        </Typography>
      </Alert>
    </Container>
  );
};

export default ExternalServicesSettings;
