import { useCallback, useEffect, useMemo, useState } from 'react';
import plusIcon from '@iconify-icons/mdi/plus-circle-outline';
import robotIcon from '@iconify-icons/mdi/robot';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import accountTieIcon from '@iconify-icons/mdi/account-tie-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import {
  Alert,
  alpha,
  Box,
  Button,
  Chip,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Fade,
  IconButton,
  Paper,
  Skeleton,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';

import { Iconify } from 'src/components/iconify';

import SlackBotConfigDialog from './components/slack-bot-config-dialog';
import {
  slackBotConfigService,
  type AgentOption,
  type SlackBotConfig,
  type SlackBotConfigPayload,
} from './services/slack-bot-config';

export default function SlackBotSettings() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [configs, setConfigs] = useState<SlackBotConfig[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingConfig, setEditingConfig] = useState<SlackBotConfig | null>(null);
  const [deleteDialogConfig, setDeleteDialogConfig] = useState<SlackBotConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const agentMap = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.name])),
    [agents],
  );

  const loadData = useCallback(async (showRefreshIndicator = false) => {
    if (showRefreshIndicator) {
      setIsRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const [loadedConfigs, loadedAgents] = await Promise.all([
        slackBotConfigService.getConfigs(),
        slackBotConfigService.getAgents(),
      ]);
      setConfigs(loadedConfigs);
      setAgents(loadedAgents);
      setError(null);
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || 'Failed to load Slack Bot settings');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const openAddDialog = () => {
    setEditingConfig(null);
    setDialogOpen(true);
  };

  const openEditDialog = (config: SlackBotConfig) => {
    setEditingConfig(config);
    setDialogOpen(true);
  };

  const closeDialog = () => {
    if (saving) return;
    setDialogOpen(false);
    setEditingConfig(null);
  };

  const handleSave = async (data: SlackBotConfigPayload) => {
    setSaving(true);
    try {
      if (editingConfig) {
        await slackBotConfigService.updateConfig(editingConfig.id, data);
        setSuccess('Slack Bot configuration updated successfully.');
      } else {
        await slackBotConfigService.createConfig(data);
        setSuccess('Slack Bot configuration added successfully.');
      }
      setDialogOpen(false);
      setEditingConfig(null);
      await loadData(true);
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || 'Failed to save Slack Bot configuration');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (configId: string) => {
    try {
      await slackBotConfigService.deleteConfig(configId);
      setSuccess('Slack Bot configuration deleted successfully.');
      await loadData(true);
      setDeleteDialogConfig(null);
    } catch (err: any) {
      setError(
        err?.response?.data?.message || err?.message || 'Failed to delete Slack Bot configuration',
      );
    }
  };

  const openDeleteDialog = (config: SlackBotConfig) => {
    setDeleteDialogConfig(config);
  };

  const closeDeleteDialog = () => {
    if (saving) return;
    setDeleteDialogConfig(null);
  };


  const totalBots = configs.length;
  const linkedAgents = new Set(configs.map((config) => config.agentId)).size;

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
          position: 'relative',
          backgroundColor: theme.palette.background.paper,
        }}
      >
        {isRefreshing && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              zIndex: 1000,
              overflow: 'hidden',
            }}
          >
            <Box
              sx={{
                height: '100%',
                width: '30%',
                backgroundColor: theme.palette.primary.main,
                animation: 'loading-slide 1.5s ease-in-out infinite',
                '@keyframes loading-slide': {
                  '0%': { transform: 'translateX(-100%)' },
                  '100%': { transform: 'translateX(400%)' },
                },
              }}
            />
          </Box>
        )}

        <Box
          sx={{
            px: 3,
            py: 3,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDark
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.grey[50], 0.5),
          }}
        >
          <Fade in={!loading} timeout={600}>
            <Stack spacing={2}>
              <Stack
                direction={{ xs: 'column', md: 'row' }}
                justifyContent="space-between"
                alignItems={{ xs: 'flex-start', md: 'center' }}
                gap={2}
              >
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <Box
                    sx={{
                      width: 40,
                      height: 40,
                      borderRadius: 1.5,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      backgroundColor: alpha(theme.palette.primary.main, 0.1),
                      border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                    }}
                  >
                    <Box
                      component="img"
                      src="/assets/icons/connectors/slack.svg"
                      alt="Slack"
                      sx={{ width: 20, height: 20, objectFit: 'contain' }}
                    />
                  </Box>
                  <Box>
                    <Typography variant="h5" sx={{ fontWeight: 700, fontSize: '1.5rem' }}>
                      Slack Bots
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Configure Slack credentials and map each bot to your agents.
                    </Typography>
                  </Box>
                </Stack>

                <Stack direction="row" spacing={1} alignItems="center">
                  {totalBots > 0 && (
                    <>
                      <Chip
                        size="small"
                        label={`${totalBots} Bots`}
                        sx={{
                          height: 28,
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          backgroundColor: isDark
                            ? alpha(theme.palette.primary.main, 0.8)
                            : alpha(theme.palette.primary.main, 0.1),
                          color: isDark ? theme.palette.common.white : theme.palette.primary.main,
                          border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                        }}
                      />
                      <Chip
                        size="small"
                        label={`${linkedAgents} Agents`}
                        sx={{
                          height: 28,
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          backgroundColor: isDark
                            ? alpha(theme.palette.info.main, 0.8)
                            : alpha(theme.palette.info.main, 0.1),
                          color: isDark ? theme.palette.info.contrastText : theme.palette.info.main,
                          border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                        }}
                      />
                    </>
                  )}
                  <Tooltip title="Refresh list" arrow>
                    <IconButton
                      size="small"
                      onClick={() => loadData(true)}
                      disabled={isRefreshing}
                      sx={{
                        width: 32,
                        height: 32,
                        backgroundColor: isDark
                          ? alpha(theme.palette.background.default, 0.4)
                          : theme.palette.background.paper,
                        border: `1px solid ${theme.palette.divider}`,
                        '&:hover': {
                          backgroundColor: alpha(theme.palette.primary.main, 0.08),
                          borderColor: theme.palette.primary.main,
                        },
                      }}
                    >
                      <Iconify
                        icon={refreshIcon}
                        width={16}
                        sx={{
                          color: theme.palette.text.secondary,
                          ...(isRefreshing && {
                            animation: 'spin 1s linear infinite',
                            '@keyframes spin': {
                              '0%': { transform: 'rotate(0deg)' },
                              '100%': { transform: 'rotate(360deg)' },
                            },
                          }),
                        }}
                      />
                    </IconButton>
                  </Tooltip>
                  <Button
                    variant="contained"
                    startIcon={<Iconify icon={plusIcon} width={18} />}
                    onClick={openAddDialog}
                    sx={{ textTransform: 'none', fontWeight: 600, borderRadius: 1.5, px: 2.2 }}
                  >
                    Add Slack Bot
                  </Button>
                </Stack>
              </Stack>
            </Stack>
          </Fade>
        </Box>

        <Box sx={{ p: 3 }}>
          {loading ? (
            <Stack spacing={2.5}>
              <Skeleton variant="text" height={32} width={220} />
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
                  gap: 2,
                }}
              >
                {[1, 2, 3].map((item) => (
                  <Skeleton
                    key={item}
                    variant="rectangular"
                    height={185}
                    sx={{ borderRadius: 1.5 }}
                  />
                ))}
              </Box>
            </Stack>
          ) : configs.length === 0 ? (
            <Alert severity="info">
              No Slack Bot configuration found. Click <strong>Add Slack Bot</strong> to create one.
            </Alert>
          ) : (
            <Fade in timeout={600}>
              <Stack spacing={2.5}>
                <Box
                  sx={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 255px))',
                    gap: 2,
                    justifyContent: 'flex-start',
                  }}
                >
                  {configs.map((config) => (
                    <Paper
                      key={config.id}
                      variant="outlined"
                      sx={{
                        p: 1.75,
                        borderRadius: 2,
                        minHeight: 300,
                        display: 'flex',
                        borderColor: isDark
                          ? alpha(theme.palette.primary.main, 0.45)
                          : alpha(theme.palette.primary.main, 0.35),
                        // background: isDark
                        //   ? `linear-gradient(170deg, ${alpha('#202831', 0.98)} 0%, ${alpha('#182c43', 0.98)} 100%)`
                        //   : `linear-gradient(170deg, ${alpha('#14263a', 0.95)} 0%, ${alpha('#1c3552', 0.95)} 100%)`,
                        transition: theme.transitions.create(['box-shadow', 'border-color']),
                        '&:hover': {
                          borderColor: alpha(theme.palette.primary.main, 0.6),
                          boxShadow: `0 10px 20px ${alpha(theme.palette.primary.main, isDark ? 0.28 : 0.2)}`,
                        },
                      }}
                    >
                      <Stack
                        spacing={1.75}
                        sx={{
                          width: '100%',
                          height: '100%',
                          justifyContent: 'space-between',
                        }}
                      >
                        <Stack alignItems="center" spacing={1.25} sx={{ pt: 0.5 }}>
                          <Box
                            sx={{
                              width: 72,
                              height: 72,
                              borderRadius: '50%',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              bgcolor: isDark
                                ? alpha(theme.palette.common.white, 0.9)
                                : alpha(theme.palette.common.white, 0.95),
                              border: `1px solid ${alpha(theme.palette.common.white, 0.35)}`,
                            }}
                          >
                            <Iconify icon={robotIcon} width={30} sx={{ color: alpha(theme.palette.grey[700], 0.85) }} />
                          </Box>

                          <Typography
                            variant="h6"
                            sx={{
                              fontWeight: 700,
                              textAlign: 'center',
                              maxWidth: '100%',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                            title={config.name}
                          >
                            {config.name}
                          </Typography>

                          <Chip
                            size="small"
                            icon={<Iconify icon={accountTieIcon} width={14} />}
                            label={`Agent : ${agentMap.get(config.agentId) || config.agentId}`}
                            sx={{
                              maxWidth: '100%',
                              borderRadius: 1,
                              bgcolor: isDark
                                ? alpha(theme.palette.success.main, 0.28)
                                : alpha(theme.palette.success.main, 0.12),
                              border: `1px solid ${alpha(theme.palette.success.main, isDark ? 0.5 : 0.3)}`,
                              color: theme.palette.common.white,
                              '& .MuiChip-icon': {
                                color: theme.palette.common.white,
                              },
                              '& .MuiChip-label': {
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              },
                            }}
                            title={agentMap.get(config.agentId) || config.agentId}
                          />
                        </Stack>

                        <Stack spacing={1} sx={{ pt: 1 }}>
                          <Button
                            fullWidth
                            variant="outlined"
                            startIcon={<Iconify icon={eyeIcon} width={18} />}
                            onClick={() => openEditDialog(config)}
                            sx={{
                              height: 44,
                              borderRadius: 1.5,
                              textTransform: 'none',
                              fontWeight: 700,
                              borderColor: alpha(theme.palette.primary.main, 0.55),
                              color: isDark ? alpha(theme.palette.common.white, 0.96) : theme.palette.common.white,
                              backgroundColor: alpha(theme.palette.primary.main, isDark ? 0.06 : 0.08),
                              '&:hover': {
                                borderColor: alpha(theme.palette.primary.main, 0.8),
                                backgroundColor: alpha(theme.palette.primary.main, 0.16),
                              },
                            }}
                          >
                            Manage
                          </Button>
                          <Button
                            fullWidth
                            variant="outlined"
                            startIcon={<Iconify icon={deleteIcon} width={18} />}
                            onClick={() => openDeleteDialog(config)}
                            color="error"
                            sx={{
                              height: 44,
                              borderRadius: 1.5,
                              textTransform: 'none',
                              fontWeight: 700,
                              borderColor: alpha(theme.palette.error.main, 0.5),
                              color: theme.palette.error.main,
                              '&:hover': {
                                borderColor: theme.palette.error.main,
                                backgroundColor: alpha(theme.palette.error.main, 0.08),
                              },
                            }}
                          >
                            Delete
                          </Button>

                        </Stack>
                      </Stack>
                    </Paper>
                  ))}
                </Box>

                <Alert
                  variant="outlined"
                  severity="info"
                  sx={{
                    borderRadius: 1.5,
                    borderColor: alpha(theme.palette.info.main, 0.2),
                    backgroundColor: alpha(theme.palette.info.main, 0.04),
                  }}
                >
                  Configure each bot with its own credentials and agent mapping for clean channel-wise control.
                </Alert>
              </Stack>
            </Fade>
          )}
        </Box>
      </Box>

      <SlackBotConfigDialog
        open={dialogOpen}
        loading={saving}
        agents={agents}
        initialData={editingConfig}
        onClose={closeDialog}
        onSubmit={handleSave}
      />

      <Dialog open={!!deleteDialogConfig} onClose={closeDeleteDialog} fullWidth maxWidth="xs">
        <DialogTitle>Delete Slack Bot</DialogTitle>
        <DialogContent>
          <Typography variant="body1">
            Are you sure you want to delete {deleteDialogConfig?.name}?
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button onClick={closeDeleteDialog} disabled={saving}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => deleteDialogConfig && handleDelete(deleteDialogConfig.id)}
            disabled={saving}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={!!error}
        autoHideDuration={6000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 7 }}
      >
        <Alert onClose={() => setError(null)} severity="error" variant="filled">
          {error}
        </Alert>
      </Snackbar>

      <Snackbar
        open={!!success}
        autoHideDuration={4000}
        onClose={() => setSuccess(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 7 }}
      >
        <Alert onClose={() => setSuccess(null)} severity="success" variant="filled">
          {success}
        </Alert>
      </Snackbar>
    </Container>
  );
}
