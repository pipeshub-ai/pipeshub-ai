import React, { useState, useMemo } from 'react';
import {
  Box,
  Grid,
  Card,
  CardContent,
  Avatar,
  Button,
  Typography,
  alpha,
  useTheme,
  Chip,
  TextField,
  InputAdornment,
  Stack,
  Tooltip,
} from '@mui/material';
import addIcon from '@iconify-icons/mdi/plus';
import robotIcon from '@iconify-icons/mdi/robot';
import searchIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import settingsIcon from '@iconify-icons/mdi/settings';
import boltIcon from '@iconify-icons/mdi/bolt';
import { Iconify } from 'src/components/iconify';
import { AVAILABLE_MODEL_PROVIDERS, ModelProvider, ModelType } from '../types';

interface ProviderCardsProps {
  onProviderSelect: (provider: ModelProvider, modelType?: ModelType) => void;
  configuredProviders: { [key: string]: { llm: number; embedding: number } };
}

// Compact capabilities mapping - only show most important ones
const PROVIDER_CAPABILITIES = {
  openAI: ['CHAT', 'EMBEDDING'],
  anthropic: ['CHAT'],
  gemini: ['CHAT', 'EMBEDDING'],
  azureOpenAI: ['CHAT', 'EMBEDDING'],
  cohere: ['CHAT', 'EMBEDDING'],
  ollama: ['CHAT', 'EMBEDDING'],
  mistral: ['CHAT'],
  huggingface: ['EMBEDDING'],
  xai: ['CHAT'],
  together: ['CHAT', 'EMBEDDING'],
  groq: ['CHAT', 'HIGH SPEED'],
  fireworks: ['CHAT', 'EMBEDDING'],
  openAICompatible: ['CHAT', 'EMBEDDING'],
  bedrock: ['CHAT', 'EMBEDDING'],
  sentenceTransformers: ['EMBEDDING'],
  jinaAI: ['EMBEDDING'],
  voyage: ['EMBEDDING'],
  default: ['EMBEDDING'],
} as const;

const ProviderCards: React.FC<ProviderCardsProps> = ({ onProviderSelect, configuredProviders }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [searchQuery, setSearchQuery] = useState('');

  // Filter providers based on search query
  const filteredProviders = useMemo(() => {
    if (!searchQuery.trim()) {
      return AVAILABLE_MODEL_PROVIDERS;
    }

    const query = searchQuery.toLowerCase().trim();
    return AVAILABLE_MODEL_PROVIDERS.filter((provider) => {
      // Search by provider ID
      if (provider.id.toLowerCase().includes(query)) {
        return true;
      }

      // Search by provider name
      if (provider.name.toLowerCase().includes(query)) {
        return true;
      }

      // Search by description
      if (provider.description.toLowerCase().includes(query)) {
        return true;
      }

      // Search by supported types
      if (provider.supportedTypes.some((type) => type.toLowerCase().includes(query))) {
        return true;
      }

      // Search by capabilities
      const capabilities =
        PROVIDER_CAPABILITIES[provider.id as keyof typeof PROVIDER_CAPABILITIES] || [];
      if (capabilities.some((cap) => cap.toLowerCase().includes(query))) {
        return true;
      }

      return false;
    });
  }, [searchQuery]);

  const handleClearSearch = () => {
    setSearchQuery('');
  };

  const getStatusConfig = (totalConfigured: number) => {
    if (totalConfigured > 0) {
      return {
        label: 'Configured',
        color: theme.palette.success.main,
        bgColor: isDark ? alpha(theme.palette.success.main, 0.8) : alpha(theme.palette.success.main, 0.1),
        icon: checkCircleIcon,
      };
    }
    return {
      label: 'Setup Required',
      color: theme.palette.text.secondary,
      bgColor: isDark ? alpha(theme.palette.text.secondary, 0.8) : alpha(theme.palette.text.secondary, 0.08),
      icon: settingsIcon,
    };
  };

  return (
    <Box>
      {/* Search Input */}
      <Box sx={{ mb: 3, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Available Models
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', flexWrap: 'wrap' }}>
          <TextField
            fullWidth
            placeholder="Search providers by name, ID, or capabilities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            variant="outlined"
            size="small"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon={searchIcon} width={20} height={20} />
                </InputAdornment>
              ),
              endAdornment: searchQuery && (
                <InputAdornment position="end">
                  <Button
                    size="small"
                    onClick={handleClearSearch}
                    sx={{
                      minWidth: 'auto',
                      p: 0.5,
                      color: theme.palette.text.secondary,
                      '&:hover': {
                        bgcolor: alpha(theme.palette.text.secondary, 0.1),
                      },
                    }}
                  >
                    <Iconify icon={clearIcon} width={16} height={16} />
                  </Button>
                </InputAdornment>
              ),
            }}
            sx={{
              width: '100%',
              minWidth: '380px',
              '& .MuiOutlinedInput-root': {
                bgcolor: theme.palette.background.paper,
                borderRadius: '8px',
                '& fieldset': {
                  borderColor: theme.palette.divider,
                },
                '&:hover fieldset': {
                  borderColor: alpha(theme.palette.primary.main, 0.5),
                },
                '&.Mui-focused fieldset': {
                  borderColor: theme.palette.primary.main,
                },
              },
            }}
          />
          {/* Search Results Info */}
          {searchQuery && (
            <Typography
              variant="body2"
              sx={{
                mt: 1,
                color: theme.palette.text.secondary,
                fontSize: '0.875rem',
              }}
            >
              {filteredProviders.length === 0
                ? `No providers found for "${searchQuery}"`
                : filteredProviders.length === 1
                  ? `1 provider found`
                  : `${filteredProviders.length} providers found`}
            </Typography>
          )}
        </Box>
      </Box>

      {/* Provider Cards Grid */}
      {filteredProviders.length === 0 ? (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            py: 8,
            color: theme.palette.text.secondary,
          }}
        >
          <Iconify icon={searchIcon} width={48} height={48} sx={{ mb: 2, opacity: 0.5 }} />
          <Typography variant="h6" sx={{ mb: 1 }}>
            No providers found
          </Typography>
          <Typography variant="body2" align="center">
            Try adjusting your search terms or{' '}
            <Button
              variant="text"
              size="small"
              onClick={handleClearSearch}
              sx={{ textTransform: 'none', p: 0, minWidth: 'auto' }}
            >
              clear the search
            </Button>
          </Typography>
        </Box>
      ) : (
        <Grid container spacing={2.5}>
          {filteredProviders.map((provider) => {
            const configCount = configuredProviders[provider.id];
            const llmCount = configCount?.llm || 0;
            const embeddingCount = configCount?.embedding || 0;
            const totalConfigured = llmCount + embeddingCount;
            const hasLlm = provider.supportedTypes.includes('llm');
            const hasEmbedding = provider.supportedTypes.includes('embedding');
            const capabilities =
              PROVIDER_CAPABILITIES[provider.id as keyof typeof PROVIDER_CAPABILITIES] ||
              provider.supportedTypes.map((type) => type.toUpperCase());

            const statusConfig = getStatusConfig(totalConfigured);

            return (
              <Grid item xs={12} sm={6} md={4} lg={3} key={provider.id}>
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
                    cursor: 'pointer',
                    transition: theme.transitions.create(
                      ['transform', 'box-shadow', 'border-color'],
                      {
                        duration: theme.transitions.duration.shorter,
                        easing: theme.transitions.easing.easeOut,
                      }
                    ),
                    position: 'relative',
                    '&:hover': {
                      transform: 'translateY(-2px)',
                      borderColor: alpha(theme.palette.primary.main, 0.5),
                      boxShadow: isDark
                        ? `0 8px 32px ${alpha('#000', 0.3)}`
                        : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
                      '& .provider-avatar': {
                        transform: 'scale(1.05)',
                      },
                    },
                  }}
                >
                  {/* Status Dot */}
                  {totalConfigured > 0 && (
                    <Box
                      sx={{
                        position: 'absolute',
                        top: 12,
                        right: 12,
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        backgroundColor: theme.palette.success.main,
                        boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
                      }}
                    />
                  )}

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
                    {/* Header */}
                    <Stack spacing={1.5} alignItems="center">
                      <Avatar
                        className="provider-avatar"
                        sx={{
                          width: 48,
                          height: 48,
                          backgroundColor: isDark
                            ? alpha(theme.palette.common.white, 0.9)
                            : alpha(theme.palette.grey[100], 0.8),
                          border: `1px solid ${theme.palette.divider}`,
                          transition: theme.transitions.create('transform'),
                        }}
                      >
                        {provider.src ? (
                          <img 
                            src={provider.src} 
                            alt={provider.name} 
                            width={24} 
                            height={24}
                            style={{ objectFit: 'contain' }}
                          />
                        ) : (
                          <Iconify icon={robotIcon} width={24} height={24} />
                        )}
                      </Avatar>

                      <Box sx={{ textAlign: 'center', width: '100%' }}>
                        <Typography
                          variant="subtitle2"
                          sx={{
                            fontWeight: 600,
                            color: theme.palette.text.primary,
                            mb: 0.25,
                            lineHeight: 1.2,
                          }}
                        >
                          {provider.name}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{
                            color: theme.palette.text.secondary,
                            fontSize: '0.8125rem',
                          }}
                        >
                          AI Provider
                        </Typography>
                      </Box>
                    </Stack>

                    {/* Status */}
                    <Box sx={{ display: 'flex', justifyContent: 'center' }}>
                      <Chip
                        icon={<Iconify icon={statusConfig.icon} width={14} height={14} />}
                        label={statusConfig.label}
                        size="small"
                        sx={{
                          height: 24,
                          fontSize: '0.75rem',
                          fontWeight: 500,
                          backgroundColor: statusConfig.bgColor,
                          color: statusConfig.color,
                          border: `1px solid ${alpha(statusConfig.color, 0.2)}`,
                          '& .MuiChip-icon': {
                            color: statusConfig.color,
                          },
                        }}
                      />
                    </Box>

                    {/* Capabilities */}
                    <Stack 
                      direction="row" 
                      spacing={0.5} 
                      justifyContent="center" 
                      alignItems="center"
                      sx={{ minHeight: 20, flexWrap: 'wrap', gap: 0.5 }}
                    >
                      {capabilities.slice(0, 3).map((capability, index) => (
                        <Typography
                          key={index}
                          variant="caption"
                          sx={{
                            px: 1,
                            py: 0.25,
                            borderRadius: 0.5,
                            fontSize: '0.6875rem',
                            fontWeight: 500,
                            color: theme.palette.text.secondary,
                            backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                            border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
                          }}
                        >
                          {capability}
                        </Typography>
                      ))}
                      
                    </Stack>

                    {/* Model Count Status */}
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        px: 1.5,
                        py: 1,
                        borderRadius: 1,
                        backgroundColor: isDark 
                          ? alpha(theme.palette.background.default, 0.3) 
                          : alpha(theme.palette.grey[50], 0.8),
                        border: `1px solid ${theme.palette.divider}`,
                      }}
                    >
                      <Stack direction="row" spacing={0.5} alignItems="center">
                        <Box
                          sx={{
                            width: 4,
                            height: 4,
                            borderRadius: '50%',
                            backgroundColor: llmCount > 0 
                              ? theme.palette.success.main 
                              : theme.palette.text.disabled,
                          }}
                        />
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: '0.75rem',
                            fontWeight: 500,
                            color: theme.palette.text.secondary,
                          }}
                        >
                          {llmCount > 0 ? `${llmCount} LLM` : 'No LLM'}
                        </Typography>
                      </Stack>
                      
                      <Stack direction="row" spacing={0.5} alignItems="center">
                        <Box
                          sx={{
                            width: 4,
                            height: 4,
                            borderRadius: '50%',
                            backgroundColor: embeddingCount > 0 
                              ? theme.palette.warning.main 
                              : theme.palette.text.disabled,
                          }}
                        />
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: '0.75rem',
                            fontWeight: 500,
                            color: theme.palette.text.secondary,
                          }}
                        >
                          {embeddingCount > 0 ? `${embeddingCount} Embed` : 'No Embed'}
                        </Typography>
                      </Stack>
                    </Box>

                    {/* Action Buttons */}
                    <Stack spacing={1} sx={{ mt: 'auto' }}>
                      {hasLlm && (
                        <Button
                          fullWidth 
                          variant="outlined"
                          size="medium"
                          startIcon={<Iconify icon={addIcon} width={16} height={16} />}
                          onClick={(e) => {
                            e.stopPropagation();
                            onProviderSelect(provider, 'llm');
                          }}
                          sx={{
                            height: 38,
                            borderRadius: 1.5,
                            textTransform: 'none',
                            fontWeight: 600,
                            fontSize: '0.8125rem',
                            borderColor: alpha(theme.palette.primary.main, 0.3),
                            '&:hover': {
                              borderColor: theme.palette.primary.main,
                              backgroundColor: alpha(theme.palette.primary.main, 0.04),
                            },
                          }}
                        >
                          Add LLM
                        </Button>
                      )}
                      
                      {hasEmbedding && (
                        <Button
                          fullWidth 
                          variant="outlined"
                          size="medium"
                          startIcon={<Iconify icon={addIcon} width={16} height={16} />}
                          onClick={(e) => {
                            e.stopPropagation();
                            onProviderSelect(provider, 'embedding');
                          }}
                          sx={{
                            height: 38,
                            borderRadius: 1.5,
                            textTransform: 'none',
                            fontWeight: 600,
                            fontSize: '0.8125rem',
                            borderColor: alpha(theme.palette.primary.main, 0.3),
                            '&:hover': {
                              borderColor: theme.palette.primary.main,
                              backgroundColor: alpha(theme.palette.primary.main, 0.04),
                            },
                          }}
                        >
                          Add Embedding
                        </Button>
                      )}
                    </Stack>
                  </CardContent>
                </Card>
              </Grid>
            );
          })}
        </Grid>
      )}
    </Box>
  );
};

export default ProviderCards;