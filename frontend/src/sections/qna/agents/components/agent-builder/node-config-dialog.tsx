// src/sections/agents/components/node-config-dialog.tsx
import React, { useState, useEffect, useCallback, memo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Switch,
  FormControlLabel,
  Grid,
  Box,
  Typography,
  IconButton,
  useTheme,
  alpha,
  Autocomplete,
  Chip,
  Avatar,
  Stack,
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/eva/close-outline';
import cogIcon from '@iconify-icons/mdi/cog';
import personIcon from '@iconify-icons/eva/person-add-fill';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import shieldIcon from '@iconify-icons/mdi/shield-check-outline';
import { useUsers } from 'src/context/UserContext';
import { useGroups } from 'src/context/GroupsContext';
import { userChipStyle, groupChipStyle } from '../../utils/agent';

interface NodeConfigDialogProps {
  node: any;
  open: boolean;
  onClose: () => void;
  onSave: (nodeId: string, config: Record<string, any>) => void;
  onDelete: (nodeId: string) => void;
}

// Helper function to get initials
const getInitials = (fullName: string) =>
  fullName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase();

const NodeConfigDialog: React.FC<NodeConfigDialogProps> = memo(
  ({ node, open, onClose, onSave, onDelete }) => {
    const theme = useTheme();
    const isDark = theme.palette.mode === 'dark';
    const [config, setConfig] = useState<Record<string, any>>({});
    const [isInitialized, setIsInitialized] = useState(false);
    const users = useUsers();
    const groups = useGroups();

    // Initialize config only when dialog opens with a valid node
    useEffect(() => {
      if (open && node && !isInitialized) {
        setConfig(node.data.config || {});
        setIsInitialized(true);
      } else if (!open) {
        setIsInitialized(false);
        setConfig({});
      }
    }, [open, node, isInitialized]);

    const handleSave = useCallback(() => {
      if (node) {
        onSave(node.id, config);
        onClose();
      }
    }, [node, config, onSave, onClose]);

    const handleClose = useCallback(() => {
      onClose();
    }, [onClose]);

    const handleDelete = useCallback(() => {
      if (node) {
        onDelete(node.id);
        onClose();
      }
    }, [node, onDelete, onClose]);

    // Get avatar color based on name
    const getAvatarColor = useCallback((name: string) => {
      const colors = [
        theme.palette.primary.main,
        theme.palette.info.main,
        theme.palette.success.main,
        theme.palette.warning.main,
        theme.palette.error.main,
      ];

      const hash = name.split('').reduce((acc, char) => char.charCodeAt(0) + (acc * 32 - acc), 0);
      return colors[Math.abs(hash) % colors.length];
    }, [theme]);

    const renderConfigField = useCallback(
      (key: string, value: any) => {
        // Special handling for Agent configuration
        if (node?.data.type === 'agent-core') {
          if (key === 'systemPrompt') {
            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  System Prompt
                </Typography>
                <TextField
                  fullWidth
                  multiline
                  rows={4}
                  value={value}
                  onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.value }))}
                  placeholder="Define the agent's role, capabilities, and behavior instructions..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: isDark
                          ? theme.palette.primary.main
                          : theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    },
                  }}
                />
              </Grid>
            );
          }
          if (key === 'startMessage') {
            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Starting Message
                </Typography>
                <TextField
                  fullWidth
                  multiline
                  rows={2}
                  value={value}
                  onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.value }))}
                  placeholder="Enter the agent's greeting message to users..."
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    },
                  }}
                />
              </Grid>
            );
          }
          if (key === 'routing') {
            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Routing Strategy
                </Typography>
                <FormControl fullWidth>
                  <Select
                    value={value}
                    onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.value }))}
                    sx={{
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    }}
                  >
                    <MenuItem value="auto">Auto (Intelligent Routing)</MenuItem>
                    <MenuItem value="sequential">Sequential Processing</MenuItem>
                    <MenuItem value="parallel">Parallel Processing</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
            );
          }
          if (key === 'allowMultipleLLMs') {
            return (
              <Grid item xs={12} key={key}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={Boolean(value)}
                      onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.checked }))}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        Allow Multiple LLMs
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Enable the agent to use multiple language models simultaneously
                      </Typography>
                    </Box>
                  }
                />
              </Grid>
            );
          }
        }

        // Special handling for Tool approval configuration with full user/group selection
        if (node?.data.type.startsWith('tool-') && !node?.data.type.startsWith('tool-group-')) {
          if (key === 'approvalConfig') {
            const selectedUsers = users
              ? users.filter((user) => value?.approvers?.users?.includes(user._id))
              : [];
            const selectedGroups = groups
              ? groups.filter((group) => value?.approvers?.groups?.includes(group._id))
              : [];

            return (
              <Grid item xs={12} key={key}>
                <Box
                  sx={{
                    p: 2,
                    border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                    borderRadius: 2,
                    backgroundColor: alpha(theme.palette.background.paper, 0.5),
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <Icon
                      icon={shieldIcon}
                      width={20}
                      height={20}
                      style={{ color: theme.palette.warning.main }}
                    />
                    <Typography variant="body2" color="text.primary" sx={{ fontWeight: 600 }}>
                      Approval Settings
                    </Typography>
                  </Box>

                  <FormControlLabel
                    control={
                      <Switch
                        checked={Boolean(value?.requiresApproval)}
                        onChange={(e) =>
                          setConfig((prev) => ({
                            ...prev,
                            [key]: {
                              ...prev[key],
                              requiresApproval: e.target.checked,
                              approvers: prev[key]?.approvers || { users: [], groups: [] },
                              approvalThreshold: prev[key]?.approvalThreshold || 'single',
                            },
                          }))
                        }
                        color="warning"
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                          Require Approval for Tool Actions
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          Enable approval workflow before tool execution
                        </Typography>
                      </Box>
                    }
                    sx={{ mb: 2 }}
                  />

                  {value?.requiresApproval && (
                    <Box>
                      {/* Users Selection */}
                      <Box sx={{ mb: 2 }}>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mb: 1, fontWeight: 500 }}
                        >
                          Select Users
                        </Typography>
                        <Autocomplete
                          multiple
                          limitTags={3}
                          options={users || []}
                          getOptionLabel={(option) => option.fullName || 'Unknown User'}
                          value={selectedUsers}
                          onChange={(_, newValue) => {
                            setConfig((prev) => ({
                              ...prev,
                              [key]: {
                                ...prev[key],
                                approvers: {
                                  ...prev[key]?.approvers,
                                  users: newValue.map((user) => user._id),
                                },
                              },
                            }));
                          }}
                          renderInput={(params) => (
                            <TextField
                              {...params}
                              placeholder="Choose users to approve..."
                              size="small"
                              sx={{
                                '& .MuiOutlinedInput-root': {
                                  borderRadius: 1,
                                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                                  '& fieldset': {
                                    borderColor: alpha(theme.palette.divider, 0.2),
                                  },
                                  '&:hover fieldset': {
                                    borderColor: alpha(theme.palette.primary.main, 0.5),
                                  },
                                },
                              }}
                              InputProps={{
                                ...params.InputProps,
                                startAdornment: (
                                  <>
                                    <Box mr={1} display="flex" alignItems="center">
                                      <Icon
                                        icon={personIcon}
                                        width={16}
                                        height={16}
                                        style={{ color: theme.palette.text.secondary }}
                                      />
                                    </Box>
                                    {params.InputProps.startAdornment}
                                  </>
                                ),
                              }}
                            />
                          )}
                          renderTags={(tagValue, getTagProps) =>
                            tagValue.map((option, index) => (
                              <Chip
                                label={option.fullName || 'Unknown User'}
                                {...getTagProps({ index })}
                                size="small"
                                sx={userChipStyle(isDark, theme)}
                              />
                            ))
                          }
                          renderOption={(props, option) => (
                            <MenuItem
                              {...props}
                              sx={{
                                py: 1,
                                px: 1.5,
                                borderRadius: 1,
                                my: 0.25,
                                '&:hover': {
                                  bgcolor: alpha(theme.palette.action.hover, 0.1),
                                },
                                '&.Mui-selected': {
                                  bgcolor: alpha(theme.palette.primary.main, 0.2),
                                  '&:hover': {
                                    bgcolor: alpha(theme.palette.primary.main, 0.25),
                                  },
                                },
                              }}
                            >
                              <Stack direction="row" alignItems="center" spacing={1.5}>
                                <Avatar
                                  sx={{
                                    width: 24,
                                    height: 24,
                                    fontSize: '0.75rem',
                                    bgcolor: getAvatarColor(option.fullName || 'U'),
                                  }}
                                >
                                  {getInitials(option.fullName || 'U')}
                                </Avatar>
                                <Box>
                                  <Typography
                                    variant="body2"
                                    sx={{
                                      fontWeight: 500,
                                      color: theme.palette.text.primary,
                                    }}
                                  >
                                    {option.fullName || 'Unknown User'}
                                  </Typography>
                                  <Typography variant="caption" color="text.secondary">
                                    {option.email || 'No email'}
                                  </Typography>
                                </Box>
                              </Stack>
                            </MenuItem>
                          )}
                        />
                      </Box>

                      {/* Groups Selection */}
                      <Box sx={{ mb: 2 }}>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mb: 1, fontWeight: 500 }}
                        >
                          Select Groups
                        </Typography>
                        <Autocomplete
                          multiple
                          limitTags={3}
                          options={groups || []}
                          getOptionLabel={(option) => option.name}
                          value={selectedGroups}
                          onChange={(_, newValue) => {
                            setConfig((prev) => ({
                              ...prev,
                              [key]: {
                                ...prev[key],
                                approvers: {
                                  ...prev[key]?.approvers,
                                  groups: newValue.map((group) => group._id),
                                },
                              },
                            }));
                          }}
                          renderInput={(params) => (
                            <TextField
                              {...params}
                              placeholder="Choose groups to approve..."
                              size="small"
                              sx={{
                                '& .MuiOutlinedInput-root': {
                                  borderRadius: 1,
                                  backgroundColor: alpha(theme.palette.background.default, 0.3),
                                  '& fieldset': {
                                    borderColor: alpha(theme.palette.divider, 0.2),
                                  },
                                  '&:hover fieldset': {
                                    borderColor: alpha(theme.palette.info.main, 0.5),
                                  },
                                },
                              }}
                              InputProps={{
                                ...params.InputProps,
                                startAdornment: (
                                  <>
                                    <Box mr={1} display="flex" alignItems="center">
                                      <Icon
                                        icon={accountGroupIcon}
                                        width={16}
                                        height={16}
                                        style={{ color: theme.palette.text.secondary }}
                                      />
                                    </Box>
                                    {params.InputProps.startAdornment}
                                  </>
                                ),
                              }}
                            />
                          )}
                          renderTags={(tagValue, getTagProps) =>
                            tagValue.map((option, index) => (
                              <Chip
                                label={option.name}
                                {...getTagProps({ index })}
                                size="small"
                                sx={groupChipStyle(isDark, theme)}
                              />
                            ))
                          }
                          renderOption={(props, option) => (
                            <MenuItem
                              {...props}
                              sx={{
                                py: 1,
                                px: 1.5,
                                borderRadius: 1,
                                my: 0.25,
                                '&:hover': {
                                  bgcolor: alpha(theme.palette.action.hover, 0.1),
                                },
                                '&.Mui-selected': {
                                  bgcolor: alpha(theme.palette.info.main, 0.2),
                                  '&:hover': {
                                    bgcolor: alpha(theme.palette.info.main, 0.25),
                                  },
                                },
                              }}
                            >
                              <Stack direction="row" alignItems="center" spacing={1.5}>
                                <Box
                                  sx={{
                                    width: 24,
                                    height: 24,
                                    borderRadius: '50%',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    bgcolor: alpha(theme.palette.info.main, 0.3),
                                  }}
                                >
                                  <Icon
                                    icon={accountGroupIcon}
                                    width={14}
                                    height={14}
                                    style={{ color: theme.palette.info.main }}
                                  />
                                </Box>
                                <Typography
                                  variant="body2"
                                  sx={{
                                    fontWeight: 500,
                                    color: theme.palette.text.primary,
                                  }}
                                >
                                  {option.name}
                                </Typography>
                              </Stack>
                            </MenuItem>
                          )}
                        />
                      </Box>

                      {/* Approval Threshold */}
                      <Box sx={{ mb: 2 }}>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mb: 1.5, fontWeight: 500 }}
                        >
                          Approval Threshold
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1 }}>
                          {[
                            { value: 'single', label: 'Single', description: 'Any one approver' },
                            { value: 'majority', label: 'Majority', description: 'More than 50%' },
                            {
                              value: 'unanimous',
                              label: 'Unanimous',
                              description: 'All approvers',
                            },
                          ].map((option) => (
                            <Box
                              key={option.value}
                              onClick={() =>
                                setConfig((prev) => ({
                                  ...prev,
                                  [key]: {
                                    ...prev[key],
                                    approvalThreshold: option.value,
                                  },
                                }))
                              }
                              sx={{
                                p: 1.5,
                                border: `1px solid ${
                                  value?.approvalThreshold === option.value
                                    ? theme.palette.primary.main
                                    : alpha(theme.palette.divider, 0.3)
                                }`,
                                borderRadius: 1,
                                cursor: 'pointer',
                                bgcolor:
                                  value?.approvalThreshold === option.value
                                    ? alpha(theme.palette.primary.main, 0.1)
                                    : 'transparent',
                                '&:hover': {
                                  bgcolor: alpha(theme.palette.primary.main, 0.05),
                                  borderColor: theme.palette.primary.main,
                                },
                                transition: 'all 0.2s ease',
                                flex: 1,
                                textAlign: 'center',
                              }}
                            >
                              <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                                {option.label}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                {option.description}
                              </Typography>
                            </Box>
                          ))}
                        </Box>
                      </Box>

                      {/* Auto-approve toggle */}
                      <Box sx={{ mb: 2 }}>
                        <FormControlLabel
                          control={
                            <Switch
                              checked={Boolean(value?.autoApprove)}
                              onChange={(e) =>
                                setConfig((prev) => ({
                                  ...prev,
                                  [key]: {
                                    ...prev[key],
                                    autoApprove: e.target.checked,
                                  },
                                }))
                              }
                              size="small"
                            />
                          }
                          label={
                            <Box>
                              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                Auto-approve after initial approval
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                For recurring actions, skip approval after first approval
                              </Typography>
                            </Box>
                          }
                        />
                      </Box>

                      {/* Summary */}
                      <Box
                        sx={{
                          p: 2,
                          bgcolor: alpha(theme.palette.success.main, 0.08),
                          borderRadius: 1,
                          border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                        }}
                      >
                        <Typography
                          variant="body2"
                          color="success.main"
                          sx={{ fontWeight: 500, mb: 1 }}
                        >
                          ✨ Configuration Summary
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          This tool requires approval from{' '}
                          <strong>
                            {(selectedUsers.length || 0) + (selectedGroups.length || 0)} approver(s)
                          </strong>{' '}
                          using <strong>{value?.approvalThreshold || 'single'}</strong> threshold.
                          {value?.autoApprove && ' Auto-approval is enabled for recurring actions.'}
                        </Typography>
                      </Box>
                    </Box>
                  )}
                </Box>
              </Grid>
            );
          }
        }

        // Special handling for LLM configuration
        if (node?.data.type?.startsWith('llm-')) {
          if (key === 'temperature') {
            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Temperature
                </Typography>
                <FormControl fullWidth>
                  <Select
                    value={value}
                    onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.value }))}
                    sx={{
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    }}
                  >
                    <MenuItem value={0.1}>0.1 (Very Focused)</MenuItem>
                    <MenuItem value={0.3}>0.3 (Focused)</MenuItem>
                    <MenuItem value={0.5}>0.5 (Balanced)</MenuItem>
                    <MenuItem value={0.7}>0.7 (Creative)</MenuItem>
                    <MenuItem value={1.0}>1.0 (Very Creative)</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
            );
          }
          if (key === 'maxTokens') {
            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Max Tokens
                </Typography>
                <TextField
                  fullWidth
                  type="number"
                  value={value}
                  onChange={(e) =>
                    setConfig((prev) => ({ ...prev, [key]: parseInt(e.target.value, 10) || 1000 }))
                  }
                  inputProps={{ min: 1, max: 4000 }}
                  helperText="Maximum number of tokens in the response"
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    },
                  }}
                />
              </Grid>
            );
          }
          if (key === 'isMultimodal' || key === 'isDefault') {
            return (
              <Grid item xs={12} key={key}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={Boolean(value)}
                      onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.checked }))}
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {key === 'isMultimodal' ? 'Multimodal Model' : 'Default Model'}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {key === 'isMultimodal'
                          ? 'Supports text, images, and other media types'
                          : 'Use this as the primary model for the agent'}
                      </Typography>
                    </Box>
                  }
                />
              </Grid>
            );
          }
        }

        // Special handling for Tool Group configuration
        if (node?.data.type.startsWith('tool-group-')) {
          if (key === 'selectedTools') {
            return (
              <Grid item xs={12} key={key}>
                <Box sx={{ mb: 2 }}>
                  <Typography
                    variant="body2"
                    color="text.primary"
                    sx={{
                      mb: 2,
                      fontWeight: 600,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                    }}
                  >
                    <Box
                      sx={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        bgcolor: 'primary.main',
                      }}
                    />
                    Selected Tools ({Array.isArray(value) ? value.length : 0} of{' '}
                    {node.data.config?.tools?.length || 0})
                  </Typography>
                  <Box
                    sx={{
                      maxHeight: 300,
                      overflow: 'auto',
                      border: `2px solid ${alpha(theme.palette.divider, 0.2)}`,
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.background.paper, 0.8),
                      backdropFilter: 'blur(8px)',
                      '&::-webkit-scrollbar': {
                        width: 8,
                      },
                      '&::-webkit-scrollbar-track': {
                        backgroundColor: alpha(theme.palette.background.default, 0.3),
                        borderRadius: 4,
                      },
                      '&::-webkit-scrollbar-thumb': {
                        backgroundColor: alpha(theme.palette.primary.main, 0.3),
                        borderRadius: 4,
                        '&:hover': {
                          backgroundColor: alpha(theme.palette.primary.main, 0.5),
                        },
                      },
                    }}
                  >
                    {node.data.config?.tools?.map((tool: any, index: number) => (
                      <Box
                        key={tool.toolId}
                        sx={{
                          p: 2,
                          transition: 'all 0.2s ease-in-out',
                          '&:hover': {
                            backgroundColor: alpha(theme.palette.primary.main, 0.05),
                          },
                        }}
                      >
                        <FormControlLabel
                          control={
                            <Switch
                              checked={Array.isArray(value) ? value.includes(tool.toolId) : false}
                              onChange={(e) => {
                                const currentSelected = Array.isArray(value) ? value : [];
                                const newSelected = e.target.checked
                                  ? [...currentSelected, tool.toolId]
                                  : currentSelected.filter((id) => id !== tool.toolId);
                                setConfig((prev) => ({ ...prev, [key]: newSelected }));
                              }}
                              size="small"
                              sx={{
                                '& .MuiSwitch-switchBase.Mui-checked': {
                                  color: theme.palette.primary.main,
                                },
                                '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                                  backgroundColor: theme.palette.primary.main,
                                },
                              }}
                            />
                          }
                          label={
                            <Box sx={{ ml: 1 }}>
                              <Typography
                                variant="body2"
                                sx={{ fontWeight: 600, fontSize: '0.875rem', mb: 0.5 }}
                              >
                                {tool.toolName?.replace(/_/g, ' ') || tool.fullName}
                              </Typography>
                              {tool.description && (
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  sx={{
                                    fontSize: '0.75rem',
                                    lineHeight: 1.4,
                                    display: 'block',
                                  }}
                                >
                                  {tool.description.length > 80
                                    ? `${tool.description.slice(0, 80)}...`
                                    : tool.description}
                                </Typography>
                              )}
                            </Box>
                          }
                          sx={{ width: '100%', alignItems: 'flex-start', margin: 0 }}
                        />
                      </Box>
                    ))}
                  </Box>
                </Box>
              </Grid>
            );
          }
        }

        // Special handling for KB Group configuration
        if (node?.data.type === 'kb-group') {
          if (key === 'selectedKBs') {
            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  Selected Knowledge Bases ({Array.isArray(value) ? value.length : 0} of{' '}
                  {node.data.config?.knowledgeBases?.length || 0})
                </Typography>
                <Box
                  sx={{
                    maxHeight: 200,
                    overflow: 'auto',
                    border: `1px solid ${theme.palette.divider}`,
                    borderRadius: 1,
                    p: 1,
                  }}
                >
                  {node.data.config?.knowledgeBases?.map((kb: any, index: number) => (
                    <FormControlLabel
                      key={kb.id}
                      control={
                        <Switch
                          checked={Array.isArray(value) ? value.includes(kb.id) : false}
                          onChange={(e) => {
                            const currentSelected = Array.isArray(value) ? value : [];
                            const newSelected = e.target.checked
                              ? [...currentSelected, kb.id]
                              : currentSelected.filter((id) => id !== kb.id);
                            setConfig((prev) => ({ ...prev, [key]: newSelected }));
                          }}
                          size="small"
                        />
                      }
                      label={
                        <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.85rem' }}>
                          {kb.name}
                        </Typography>
                      }
                      sx={{ width: '100%', mb: 1 }}
                    />
                  ))}
                </Box>
              </Grid>
            );
          }
        }

        // Special handling for App Memory configuration
        if (node?.data.type.startsWith('app-')) {
          if (key === 'searchScope' || key === 'fileTypes' || key === 'services') {
            const options =
              key === 'searchScope'
                ? ['all', 'channels', 'dms', 'inbox', 'sent', 'drafts']
                : key === 'fileTypes'
                  ? ['all', 'documents', 'images', 'videos', 'presentations']
                  : ['drive', 'docs', 'sheets', 'slides'];

            return (
              <Grid item xs={12} key={key}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontWeight: 500 }}>
                  {key.charAt(0).toUpperCase() + key.slice(1).replace(/([A-Z])/g, ' $1')}
                </Typography>
                <FormControl fullWidth>
                  <Select
                    value={Array.isArray(value) ? value[0] : value}
                    onChange={(e) =>
                      setConfig((prev) => ({
                        ...prev,
                        [key]: Array.isArray(value) ? [e.target.value] : e.target.value,
                      }))
                    }
                    sx={{
                      borderRadius: 1,
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: theme.palette.primary.main,
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderWidth: 1,
                      },
                    }}
                  >
                    {options.map((option) => (
                      <MenuItem key={option} value={option}>
                        {option.charAt(0).toUpperCase() + option.slice(1)}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>
            );
          }
        }

        // Skip certain technical fields
        if (
          [
            'modelKey',
            'modelName',
            'provider',
            'modelType',
            'toolId',
            'fullName',
            'appName',
            'appDisplayName',
            'tools',
            'knowledgeBases',
          ].includes(key)
        ) {
          return null;
        }

        // Default text field for other properties
        if (typeof value === 'string' || typeof value === 'number') {
          return (
            <Grid item xs={12} key={key}>
              <TextField
                fullWidth
                label={key.charAt(0).toUpperCase() + key.slice(1).replace(/([A-Z])/g, ' $1')}
                value={value}
                onChange={(e) => setConfig((prev) => ({ ...prev, [key]: e.target.value }))}
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1,
                    '&:hover .MuiOutlinedInput-notchedOutline': {
                      borderColor: theme.palette.primary.main,
                    },
                    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                      borderWidth: 1,
                    },
                  },
                }}
              />
            </Grid>
          );
        }

        return null;
        // eslint-disable-next-line react-hooks/exhaustive-deps
      },
      [
        node?.data.type,
        users,
        groups,
        getAvatarColor,
        isDark,
        node?.data.config?.tools,
        node?.data.config?.knowledgeBases,
        theme
      ]
    );

    // Don't render if no node or not open
    if (!node || !open) return null;

    return (
      <Dialog
        open={open}
        onClose={handleClose}
        maxWidth="md"
        fullWidth
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            overflow: 'hidden',
          },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            p: 2.5,
            pl: 3,
            color: theme.palette.text.primary,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
            m: 0,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
              }}
            >
              <Icon icon={cogIcon} width={18} height={18} />
            </Box>
            Configure {node.data.label}
          </Box>

          <IconButton
            onClick={handleClose}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            aria-label="close"
          >
            <Icon icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <DialogContent
          sx={{
            p: 0,
            '&.MuiDialogContent-root': {
              pt: 3,
              px: 3,
              pb: 0,
            },
          }}
        >
          <Box sx={{ mb: 3 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {node.data.description}
            </Typography>

            <Grid container spacing={3}>
              {Object.entries(config).map(([key, value]) => renderConfigField(key, value))}
            </Grid>
          </Box>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            variant="text"
            onClick={handleDelete}
            sx={{
              color: theme.palette.error.main,
              fontWeight: 500,
              '&:hover': {
                backgroundColor: alpha(theme.palette.error.main, 0.08),
              },
            }}
          >
            Delete Node
          </Button>
          <Box sx={{ flexGrow: 1 }} />
          <Button
            variant="text"
            onClick={handleClose}
            sx={{
              color: theme.palette.text.secondary,
              fontWeight: 500,
              '&:hover': {
                backgroundColor: alpha(theme.palette.divider, 0.8),
              },
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleSave}
            sx={{
              bgcolor: theme.palette.primary.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.primary.dark,
                boxShadow: 'none',
              },
              px: 3,
            }}
          >
            Save Configuration
          </Button>
        </DialogActions>
      </Dialog>
    );
  }
);

NodeConfigDialog.displayName = 'NodeConfigDialog';

export default NodeConfigDialog;
