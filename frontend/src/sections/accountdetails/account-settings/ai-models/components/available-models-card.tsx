import React from 'react';
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
} from '@mui/material';
import addIcon from '@iconify-icons/mdi/plus';
import robotIcon from '@iconify-icons/mdi/robot';
import { Iconify } from 'src/components/iconify';
import { AVAILABLE_MODEL_PROVIDERS, ModelProvider, ModelType } from '../types';

interface ProviderCardsProps {
  onProviderSelect: (provider: ModelProvider, modelType?: ModelType) => void;
  configuredProviders: { [key: string]: { llm: number; embedding: number } };
}

// Compact capabilities mapping - only show most important ones
const PROVIDER_CAPABILITIES = {
  openAI: ['TEXT', 'EMBEDDING', 'TTS'],
  anthropic: ['TEXT', 'REASONING'],
  gemini: ['TEXT', 'EMBEDDING', 'MULTIMODAL'],
  azureOpenAI: ['TEXT', 'EMBEDDING', 'ENTERPRISE'],
  cohere: ['TEXT', 'EMBEDDING', 'RERANK'],
  ollama: ['TEXT', 'EMBEDDING', 'LOCAL'],
  mistral: ['TEXT', 'MULTILINGUAL'],
  huggingface: ['EMBEDDING', 'TRANSFORMERS'],
  xai: ['TEXT', 'REASONING'],
  together: ['TEXT', 'EMBEDDING'],
  groq: ['TEXT', 'HIGH SPEED'],
  fireworks: ['TEXT', 'FAST INFERENCE'],
} as const;

const ProviderCards: React.FC<ProviderCardsProps> = ({ onProviderSelect, configuredProviders }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Enhanced dark mode support
  const cardBg = isDark ? 'rgba(32, 30, 30, 0.5)' : '#ffffff';
  const cardBorder = isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)';
  const textPrimary = isDark ? '#ffffff' : '#1f2937';

  // Theme-based chip colors with light backgrounds for all modes
  const getChipStyles = (type?: 'capability' | 'llm' | 'embedding' | 'popular' | 'success') => {
    switch (type) {
      case 'capability':
        return {
          color: isDark ? '#6b7280' : '#6b7280',
          bgcolor: 'rgba(107, 114, 128, 0.08)',
          border: 'rgba(107, 114, 128, 0.15)',
        };
      case 'llm':
        return {
          color: '#059669',
          bgcolor: 'rgba(81, 88, 86, 0.1)',
          border: '1px solid rgba(5, 150, 105, 0.2)',
        };
      case 'embedding':
        return {
          color: '#7c3aed',
          bgcolor: 'rgba(124, 58, 237, 0.1)',
          border: '1px solid rgba(124, 58, 237, 0.2)',
        };
      case 'popular':
        return {
          color: '#dc2626',
          bgcolor: 'rgba(220, 38, 38, 0.1)',
          border: '1px solid rgba(220, 38, 38, 0.2)',
        };
      case 'success':
        return {
          color: '#059669',
          bgcolor: 'rgba(5, 150, 105, 0.1)',
          border: '1px solid rgba(5, 150, 105, 0.15)',
        };
      default:
        return {
          color: isDark ? '#6b7280' : '#6b7280',
          bgcolor: 'rgba(107, 114, 128, 0.08)',
          border: 'rgba(107, 114, 128, 0.15)',
        };
    }
  };

  return (
    <Grid container spacing={2.5}>
      {AVAILABLE_MODEL_PROVIDERS.map((provider) => {
        const configCount = configuredProviders[provider.id];
        const llmCount = configCount?.llm || 0;
        const embeddingCount = configCount?.embedding || 0;
        const totalConfigured = llmCount + embeddingCount;
        const hasLlm = provider.supportedTypes.includes('llm');
        const hasEmbedding = provider.supportedTypes.includes('embedding');
        const isSingleType = provider.supportedTypes.length === 1;
        const capabilities =
          PROVIDER_CAPABILITIES[provider.id as keyof typeof PROVIDER_CAPABILITIES] ||
          provider.supportedTypes.map((type) => type.toUpperCase());

        return (
          <Grid item xs={12} sm={6} md={4} lg={3} key={provider.id}>
            <Card
              sx={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                borderRadius: '10px',
                bgcolor: cardBg,
                border: `1px solid ${totalConfigured > 0 ? 'white' : cardBorder}`,
                transition: 'all 0.2s ease-in-out',
                position: 'relative',
                overflow: 'hidden',
                '&:hover': {
                  boxShadow: `0 0 5px ${alpha(theme.palette.primary.main, 0.15)}`,
                  borderColor: alpha(theme.palette.primary.main, 0.8),
                },
              }}
            >
              {/* Status Indicator */}
              {totalConfigured > 0 && (
                <Box
                  sx={{
                    position: 'absolute',
                    top: 10,
                    right: 10,
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    bgcolor: theme.palette.success.main,
                    zIndex: 1,
                  }}
                />
              )}

              <CardContent
                sx={{
                  p: 2.5,
                  display: 'flex',
                  flexDirection: 'column',
                  height: '100%',
                  gap: 1.5,
                }}
              >
                {/* Provider Icon & Name */}
                <Box
                  sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}
                >
                  <Avatar
                    sx={{
                      width: 40,
                      height: 40,
                      bgcolor: 'white',
                      // border: `1.5px solid ${alpha(provider.color, 0.2)}`,
                    }}
                  >
                    {provider.src ? (
                      <img src={provider.src} alt={provider.name} width={22} height={22} />
                    ) : (
                      <Iconify icon={robotIcon} width={22} height={22} />
                    )}
                  </Avatar>

                  <Typography
                    variant="subtitle2"
                    align="center"
                    sx={{
                      fontWeight: 600,
                      color: textPrimary,
                      fontSize: '0.875rem',
                      lineHeight: 1.2,
                    }}
                  >
                    {provider.name}
                  </Typography>
                </Box>

                {/* Capabilities - Compact */}
                <Box
                  sx={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    justifyContent: 'center',
                    gap: 0.5,
                    mb: 1,
                  }}
                >
                  {capabilities.slice(0, 3).map((capability, index) => (
                    <Chip
                      key={index}
                      label={capability}
                      size="small"
                      sx={{
                        height: 18,
                        fontSize: '0.65rem',
                        fontWeight: 500,
                        bgcolor: theme.palette.grey[200],
                        color: theme.palette.grey[800],
                        '&:hover': {
                          bgcolor: theme.palette.grey[300],
                          color: theme.palette.grey[900],
                        },
                      }}
                    />
                  ))}
                </Box>

                {/* Model Type Support */}
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'center',
                    gap: 0.5,
                    mb: 1.5,
                  }}
                >
                  {provider.supportedTypes.map((type) => (
                    <Chip
                      key={type}
                      label={type.toUpperCase()}
                      size="small"
                      sx={{
                        height: 20,
                        fontSize: '0.65rem',
                        fontWeight: 600,
                        bgcolor: theme.palette.grey[200],
                        color: theme.palette.grey[800],
                        '&:hover': {
                          bgcolor: theme.palette.grey[300],
                          color: theme.palette.grey[900],
                        },
                      }}
                    />
                  ))}
                </Box>

                {/* Model Count Display - Compact */}
                {totalConfigured > 0 && (
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'center',
                      mb: 1.5,
                      p: 0.75,
                      ...getChipStyles('success'),
                      borderRadius: '6px',
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{
                        color: theme.palette.success.main,
                        fontWeight: 600,
                        fontSize: '0.7rem',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.5,
                      }}
                    >
                      <Box
                        sx={{
                          width: 4,
                          height: 4,
                          borderRadius: '50%',
                          bgcolor: theme.palette.success.main,
                        }}
                      />
                      {totalConfigured} configured
                    </Typography>
                  </Box>
                )}

                {/* Action Buttons - Improved for single type */}
                <Box
                  sx={{
                    mt: 'auto',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 1,
                    alignItems: 'flex-start',
                    justifyContent: 'flex-start',
                  }}
                >
                  <Box sx={{ width: '100%' }}>
                    {hasLlm && (
                      <Button
                        variant="outlined"
                        fullWidth
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          onProviderSelect(provider, 'llm');
                        }}
                        startIcon={<Iconify icon={addIcon} width={14} height={14} />}
                        sx={{
                          borderColor: alpha(theme.palette.primary.main, 0.3),
                          color: theme.palette.primary.main,
                          borderRadius: '6px',
                          textTransform: 'none',
                          fontWeight: 600,
                          fontSize: '0.75rem',
                          py: 0.75,
                          '&:hover': {
                            bgcolor: alpha(theme.palette.primary.main, 0.1),
                          },
                        }}
                      >
                        Add LLM
                      </Button>
                    )}
                  </Box>
                  <Box sx={{ width: '100%' }}>
                    {hasEmbedding && (
                      <Button
                        variant="outlined"
                        fullWidth
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          onProviderSelect(provider, 'embedding');
                        }}
                        startIcon={<Iconify icon={addIcon} width={14} height={14} />}
                        sx={{
                          borderColor: alpha(theme.palette.primary.main, 0.3),
                          color: theme.palette.primary.main,
                          borderRadius: '6px',
                          textTransform: 'none',
                          fontWeight: 600,
                          fontSize: '0.75rem',
                          py: 0.75,
                          '&:hover': {
                            bgcolor: alpha(theme.palette.primary.main, 0.1),
                          },
                        }}
                      >
                        Add Embedding
                      </Button>
                    )}
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>
        );
      })}
    </Grid>
  );
};

export default ProviderCards;
