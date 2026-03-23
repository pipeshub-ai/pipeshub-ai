import { useCallback, useEffect, useMemo, useState } from 'react';
import plusIcon from '@iconify-icons/mdi/plus-circle-outline';
import robotIcon from '@iconify-icons/mdi/robot';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import settingsIcon from '@iconify-icons/mdi/settings';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import {
  Alert,
  alpha,
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Fade,
  Grid,
  IconButton,
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

  const availableAgentsForDialog = useMemo(() => {
    const assignedAgentIds = new Set(
      configs
        .filter((config) => {
          if (!config.agentId) return false;
          if (!editingConfig) return true;
          return config.id !== editingConfig.id;
        })
        .map((config) => config.agentId as string),
    );

    const filtered = agents.filter((agent) => !assignedAgentIds.has(agent.id));

    if (
      editingConfig?.agentId &&
      !filtered.some((agent) => agent.id === editingConfig.agentId)
    ) {
      filtered.push({
        id: editingConfig.agentId,
        name: editingConfig.agentId,
      });
    }

    return filtered;
  }, [agents, configs, editingConfig]);

  const getAgentChipConfig = (isLinked: boolean) => {
    if (isLinked) {
      return {
        color: theme.palette.success.main,
        bgColor: isDark
          ? alpha(theme.palette.success.main, 0.8)
          : alpha(theme.palette.success.main, 0.1),
        icon: checkCircleIcon,
      };
    }
    return {
      color: theme.palette.text.secondary,
      bgColor: isDark
        ? alpha(theme.palette.text.secondary, 0.8)
        : alpha(theme.palette.text.secondary, 0.08),
      icon: settingsIcon,
    };
  };

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
  const linkedAgents = new Set(
    configs
      .map((config) => config.agentId)
      .filter((agentId): agentId is string => Boolean(agentId)),
  ).size;

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
                      Configure Slack credentials and optionally map each bot to an agent.
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
              {/* <Stack spacing={2.5} > */}
              <Stack spacing={2.5}>
                <Grid container spacing={2.5} >
                  {configs.map((config) => (
                    <Grid item xs={12} sm={6} md={4} lg={3} key={config.id}>
                      <Card
                        elevation={0}
                        sx={{
                          height: '100%',
                          minHeight: '320px',
                          display: 'flex',
                          flexDirection: 'column',
                          borderRadius: 2,
                          border: `1px solid ${theme.palette.divider}`,
                          backgroundColor: theme.palette.background.paper,
                          transition: theme.transitions.create(
                            ['transform', 'box-shadow', 'border-color'],
                            {
                              duration: theme.transitions.duration.shorter,
                              easing: theme.transitions.easing.easeOut,
                            },
                          ),
                          position: 'relative',
                          '&:hover': {
                            transform: 'translateY(-2px)',
                            borderColor: alpha(theme.palette.primary.main, 0.5),
                            boxShadow: isDark
                              ? `0 8px 32px ${alpha('#000', 0.3)}`
                              : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
                            '& .slack-bot-avatar': {
                              transform: 'scale(1.05)',
                            },
                          },
                        }}
                      >
                        <CardContent
                          sx={{
                            p: 2,
                            display: 'flex',
                            flexDirection: 'column',
                            height: '100%',
                            gap: 1.5,
                            '&:last-child': { pb: 2 },
                          }}
                        >
                          <Stack spacing={1.5} alignItems="center">
                            <Avatar
                              className="slack-bot-avatar"
                              sx={{
                                width: 48,
                                height: 48,
                                backgroundColor: isDark
                                  ? alpha(theme.palette.common.white, 0.9)
                                  : alpha(theme.palette.grey[100], 0.92),
                                border: `1px solid ${theme.palette.divider}`,
                                transition: theme.transitions.create('transform'),
                              }}
                            >
                              <Iconify
                                icon={robotIcon}
                                width={24}
                                height={24}
                                sx={{
                                  color: isDark
                                    ? alpha(theme.palette.grey[800], 0.92)
                                    : alpha(theme.palette.grey[700], 0.9),
                                }}
                              />
                            </Avatar>

                            <Box sx={{ textAlign: 'center', width: '100%' }}>
                              <Typography
                                variant="subtitle2"
                                sx={{
                                  fontWeight: 600,
                                  color: theme.palette.text.primary,
                                  mb: 0.25,
                                  lineHeight: 1.2,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                                title={config.name}
                              >
                                {config.name}
                              </Typography>
                              <Typography
                                variant="caption"
                                sx={{
                                  color: theme.palette.text.secondary,
                                  fontSize: '0.8125rem',
                                }}
                              >
                                Slack Bot
                              </Typography>
                            </Box>
                          </Stack>

                          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 1 }}>
                            {(() => {
                              const linkedAgentName = config.agentId
                                ? agentMap.get(config.agentId) || config.agentId
                                : '';
                              const isAgentLinked = Boolean(config.agentId);
                              const chipConfig = getAgentChipConfig(isAgentLinked);
                              const chipLabel = isAgentLinked
                                ? `Agent: ${linkedAgentName}`
                                : 'Agent not linked';

                              return (
                                <Chip
                                  size="small"
                                  icon={<Iconify icon={chipConfig.icon} width={14} height={14} />}
                                  label={chipLabel}
                                  sx={{
                                    maxWidth: '100%',
                                    height: 24,
                                    fontSize: '0.75rem',
                                    fontWeight: 500,
                                    color: chipConfig.color,
                                    backgroundColor: chipConfig.bgColor,
                                    border: `1px solid ${alpha(chipConfig.color, 0.2)}`,
                                    '& .MuiChip-icon': {
                                      color: chipConfig.color,
                                    },
                                    '& .MuiChip-label': {
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                    },
                                  }}
                                  title={isAgentLinked ? linkedAgentName : 'Setup Required'}
                                />
                              );
                            })()}
                          </Box>

                          <Stack spacing={1} sx={{ mt: 'auto' }}>
                            <Button
                              fullWidth
                              variant="outlined"
                              size="medium"
                              startIcon={<Iconify icon={editIcon} width={16} height={16} />}
                              onClick={() => openEditDialog(config)}
                              sx={{
                                height: 38,
                                borderRadius: 1.5,
                                textTransform: 'none',
                                fontWeight: 600,
                                mb: 1,
                                fontSize: '0.8125rem',
                                borderColor: alpha(theme.palette.primary.main, 0.3),
                                color: theme.palette.text.primary,
                                '&:hover': {
                                  borderColor: theme.palette.primary.main,
                                  backgroundColor: alpha(theme.palette.primary.main, 0.04),
                                },
                              }}
                            >
                              Edit
                            </Button>
                            <Button
                              fullWidth
                              variant="outlined"
                              size="medium"
                              startIcon={<Iconify icon={deleteIcon} width={16} height={16} />}
                              onClick={() => openDeleteDialog(config)}
                              sx={{
                                height: 38,
                                borderRadius: 1.5,
                                textTransform: 'none',
                                fontWeight: 600,
                                fontSize: '0.8125rem',
                                borderColor: alpha(theme.palette.error.main, 0.3),
                                color: theme.palette.error.main,
                                '&:hover': {
                                  borderColor: theme.palette.error.main,
                                  backgroundColor: alpha(theme.palette.error.main, 0.04),
                                },
                              }}
                            >
                              Delete
                            </Button>
                          </Stack>
                        </CardContent>
                      </Card>
                    </Grid>
                  ))}
                </Grid>

                <Alert
                  variant="outlined"
                  severity="info"
                  sx={{
                    borderRadius: 1.5,
                    borderColor: alpha(theme.palette.info.main, 0.2),
                    backgroundColor: alpha(theme.palette.info.main, 0.04),
                  }}
                >
                  Configure each bot with its own credentials and optional agent mapping for clean channel-wise control.
                </Alert>
              </Stack>
            </Fade>
          )}
        </Box>
      </Box>

      <SlackBotConfigDialog
        open={dialogOpen}
        loading={saving}
        agents={availableAgentsForDialog}
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
