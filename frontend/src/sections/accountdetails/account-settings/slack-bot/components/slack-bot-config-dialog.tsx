import { useEffect, useMemo, useState } from 'react';
import eyeIcon from '@iconify-icons/mdi/eye';
import eyeOffIcon from '@iconify-icons/mdi/eye-off';
import slackIcon from '@iconify-icons/mdi/slack';
import robotOutlineIcon from '@iconify-icons/mdi/robot-outline';
import shieldLockIcon from '@iconify-icons/mdi/shield-lock-outline';
import {
  Alert,
  alpha,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
  useTheme,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import type {
  AgentOption,
  SlackBotConfig,
  SlackBotConfigPayload,
} from '../services/slack-bot-config';

type Props = {
  open: boolean;
  loading: boolean;
  agents: AgentOption[];
  initialData: SlackBotConfig | null;
  onClose: () => void;
  onSubmit: (data: SlackBotConfigPayload) => Promise<void>;
};

type FormState = {
  name: string;
  botToken: string;
  signingSecret: string;
  agentId: string;
};

const EMPTY_FORM: FormState = {
  name: '',
  botToken: '',
  signingSecret: '',
  agentId: '',
};

export default function SlackBotConfigDialog({
  open,
  loading,
  agents,
  initialData,
  onClose,
  onSubmit,
}: Props) {
  const theme = useTheme();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [showBotToken, setShowBotToken] = useState(false);
  const [showSigningSecret, setShowSigningSecret] = useState(false);

  const isEdit = Boolean(initialData);

  useEffect(() => {
    if (!open) {
      setForm(EMPTY_FORM);
      setError(null);
      setShowBotToken(false);
      setShowSigningSecret(false);
      return;
    }

    if (initialData) {
      setForm({
        name: initialData.name || '',
        botToken: initialData.botToken || '',
        signingSecret: initialData.signingSecret || '',
        agentId: initialData.agentId || '',
      });
    } else {
      setForm(EMPTY_FORM);
    }
  }, [open, initialData]);

  const isValid = useMemo(
    () =>
      form.name.trim().length > 0 &&
      form.botToken.trim().length > 0 &&
      form.signingSecret.trim().length > 0,
    [form],
  );

  const handleChange = (key: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = async () => {
    if (!isValid) {
      setError('Slack bot name, bot token, and signing secret are required.');
      return;
    }

    setError(null);
    await onSubmit({
      name: form.name.trim(),
      botToken: form.botToken.trim(),
      signingSecret: form.signingSecret.trim(),
      ...(form.agentId.trim() ? { agentId: form.agentId.trim() } : {}),
    });
  };

  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onClose}
      fullWidth
      maxWidth="sm"
      PaperProps={{
        sx: {
          borderRadius: 2,
          border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
          overflow: 'hidden',
        },
      }}
    >
      <DialogTitle
        sx={{
          pb: 1.5,
          borderBottom: `1px solid ${theme.palette.divider}`,
          backgroundColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.grey[50], 0.5),
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1.2}>
          <Box
            sx={{
              width: 34,
              height: 34,
              borderRadius: 1.2,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.primary.main, 0.16),
            }}
          >
            <Iconify icon={slackIcon} width={18} sx={{ color: theme.palette.primary.main }} />
          </Box>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {isEdit ? 'Edit Slack Bot' : 'Add Slack Bot'}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Configure secure Slack credentials and optionally link an agent
            </Typography>
          </Box>
        </Stack>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          <Paper
            variant="outlined"
            sx={{
              p: 1.75,
              borderRadius: 1.5,
              borderColor: alpha(theme.palette.primary.main, 0.2),
              backgroundColor: alpha(theme.palette.primary.main, 0.03),
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <Iconify icon={robotOutlineIcon} width={16} />
              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                Bot details
              </Typography>
            </Stack>

            <TextField
              label="Slack Bot Name"
              value={form.name}
              onChange={(e) => handleChange('name', e.target.value)}
              fullWidth
              required
              helperText="Friendly name shown in the configuration list"
              sx={{
                '& .MuiOutlinedInput-root': {
                  backgroundColor: theme.palette.background.paper,
                },
              }}
            />
          </Paper>

          <Paper
            variant="outlined"
            sx={{
              p: 1.75,
              borderRadius: 1.5,
              borderColor: alpha(theme.palette.warning.main, 0.3),
              backgroundColor: alpha(theme.palette.warning.main, 0.05),
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <Iconify icon={shieldLockIcon} width={16} />
              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                Confidential credentials
              </Typography>
            </Stack>

            <Stack spacing={2}>
              <TextField
                label="Bot Token"
                type={showBotToken ? 'text' : 'password'}
                value={form.botToken}
                onChange={(e) => handleChange('botToken', e.target.value)}
                fullWidth
                required
                placeholder="Enter Bot Token"
                helperText="Stored securely and never shown in full"
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton onClick={() => setShowBotToken((prev) => !prev)} edge="end">
                        <Iconify icon={showBotToken ? eyeOffIcon : eyeIcon} />
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
                sx={{
                  '& .MuiOutlinedInput-root': {
                    backgroundColor: theme.palette.background.paper,
                  },
                }}
              />

              <TextField
                label="Signing Secret"
                type={showSigningSecret ? 'text' : 'password'}
                value={form.signingSecret}
                onChange={(e) => handleChange('signingSecret', e.target.value)}
                fullWidth
                required
                placeholder="Enter Signing Secret"
                helperText="Used to validate requests from Slack"
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton onClick={() => setShowSigningSecret((prev) => !prev)} edge="end">
                        <Iconify icon={showSigningSecret ? eyeOffIcon : eyeIcon} />
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
                sx={{
                  '& .MuiOutlinedInput-root': {
                    backgroundColor: theme.palette.background.paper,
                  },
                }}
              />
            </Stack>
          </Paper>

          <FormControl fullWidth>
            <InputLabel id="slack-agent-select-label">Agent (optional)</InputLabel>
            <Select
              labelId="slack-agent-select-label"
              value={form.agentId}
              label="Agent (optional)"
              sx={{ marginBottom: 2 }}
              onChange={(e) => handleChange('agentId', e.target.value)}
              MenuProps={{
                PaperProps: {
                  sx: {
                    maxHeight: 320,
                    overflowY: 'auto',
                  },
                },
              }}
            >
              <MenuItem value="">
                <Typography variant="body2" color="text.secondary">
                  No agent selected (optional)
                </Typography>
              </MenuItem>
              {agents.map((agent) => (
                <MenuItem key={agent.id} value={agent.id}>
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {agent.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {agent.id}
                    </Typography>
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Stack>
      </DialogContent>
      <DialogActions
        sx={{
          px: 3,
          pb: 2.5,
          pt: 2,
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.grey[50], 0.35),
        }}
      >
        <Button onClick={onClose} disabled={loading}>
          Cancel
        </Button>
        <Button
          onClick={handleSubmit}
          variant="contained"
          disabled={loading || !isValid}
          startIcon={loading ? <CircularProgress size={16} sx={{ color: 'inherit' }} /> : null}
        >
          {isEdit ? 'Save Changes' : 'Add Slack Bot'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
