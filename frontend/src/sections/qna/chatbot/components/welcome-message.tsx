// WelcomeMessage.js
import React from 'react';
import { Icon } from '@iconify/react';
import githubIcon from '@iconify-icons/mdi/github';
import graphIcon from '@iconify-icons/mdi/graph-outline';
import lightningBoltIcon from '@iconify-icons/mdi/lightning-bolt-outline';
import databaseSearchIcon from '@iconify-icons/mdi/database-search-outline';

import { Box, Grid, Fade, Zoom, Paper, useTheme, Typography } from '@mui/material';

const WelcomeMessage = () => {
  const theme = useTheme();

  // Features list - updated for enterprise knowledge base search
  const features = [
    {
      icon: databaseSearchIcon,
      title: 'Knowledge Search',
      description: 'Search across your entire knowledge base with natural language',
    },
    {
      icon: lightningBoltIcon,
      title: 'Instant Answers',
      description: 'Get precise answers extracted directly from your documents',
    },
    {
      icon: graphIcon,
      title: 'Context Awareness',
      description: 'Results understand relationships between information in your repository',
    },
  ];

  return (
    <Fade in={Boolean(true)} timeout={800}>
      <Box
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'center',
          textAlign: 'center',
          px: { xs: 2, sm: 4 },
          // py: { xs: 4, sm: 5 },
          maxWidth: '1200px',
          mx: 'auto',
        }}
      >
        {/* Logo */}
        <Zoom in={Boolean(true)} timeout={1000}>
          <Box
            sx={{
              animation: 'pulse 3s infinite ease-in-out',
              '@keyframes pulse': {
                '0%': { opacity: 0.9, transform: 'scale(1)' },
                '50%': { opacity: 1, transform: 'scale(1.05)' },
                '100%': { opacity: 0.9, transform: 'scale(1)' },
              },
            }}
          >
            <img
              src="/logo/logo-blue.svg"
              alt="Knowledge Search Logo"
              style={{
                width: '4rem',
                marginBottom: '12px',
                filter: 'drop-shadow(0 4px 6px rgba(0, 0, 0, 0.1))',
              }}
            />
          </Box>
        </Zoom>

        {/* Welcome Text */}
        <Typography
          variant="h5"
          sx={{
            fontWeight: 600,
            background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${theme.palette.secondary.main})`,
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            mb: 0.75,
            letterSpacing: '-0.01em',
          }}
        >
          Workplace AI
        </Typography>

        <Typography
          variant="subtitle1"
          sx={{
            color: theme.palette.text.secondary,
            maxWidth: '550px',
            mb: 3,
            mx: 'auto',
            lineHeight: 1.4,
            fontWeight: 400,
          }}
        >
          Instantly find answers from your organization&apos;s knowledge base
        </Typography>

        {/* Features Section */}
        <Grid
          container
          spacing={2.5}
          sx={{
            width: '100%',
            maxWidth: '950px',
            mb: 3,
          }}
        >
          {features.map((feature, index) => (
            <Grid item xs={12} md={4} key={index}>
              <Fade in={Boolean(true)} timeout={(index + 1) * 500}>
                <Paper
                  elevation={1}
                  sx={{
                    py: 2.5,
                    px: 2.5,
                    height: '100%',
                    borderRadius: 2.5,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    border: '1px solid',
                    borderColor:
                      theme.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.1)'
                        : 'rgba(0, 0, 0, 0.05)',
                    transition: 'all 0.25s ease',
                    '&:hover': {
                      transform: 'translateY(-5px)',
                      borderColor: theme.palette.primary.light,
                      boxShadow: theme.shadows[4],
                      backgroundColor:
                        theme.palette.mode === 'dark'
                          ? 'rgba(66, 133, 244, 0.08)'
                          : 'rgba(66, 133, 244, 0.04)',
                    },
                  }}
                >
                  <Icon
                    icon={feature.icon}
                    style={{
                      fontSize: '2.25rem',
                      color: theme.palette.primary.main,
                      marginBottom: '10px',
                    }}
                  />
                  <Typography
                    variant="h6"
                    gutterBottom
                    fontWeight={600}
                    sx={{ mb: 0.75, fontSize: '1.125rem' }}
                  >
                    {feature.title}
                  </Typography>
                  <Typography variant="body2" color="textSecondary" sx={{ lineHeight: 1.5 }}>
                    {feature.description}
                  </Typography>
                </Paper>
              </Fade>
            </Grid>
          ))}
        </Grid>

        {/* Example queries */}
        <Box
          sx={{
            width: '100%',
            maxWidth: '650px',
            mx: 'auto',
            p: { xs: 2, sm: 2.5 },
            borderRadius: 2,
            backgroundColor:
              theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)',
            border: `1px dashed ${
              theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)'
            }`,
          }}
        >
          <Typography
            variant="body1"
            sx={{ fontWeight: 500, mb: 1, color: theme.palette.text.primary }}
          >
            Ask a question about your knowledge base
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5 }}>
            Try queries like:
          </Typography>
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12} sm={6}>
              <Paper
                elevation={0}
                sx={{
                  p: 1.25,
                  borderRadius: 1.5,
                  backgroundColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.05)'
                      : 'rgba(0, 0, 0, 0.02)',
                  border: '1px solid',
                  borderColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.1)'
                      : 'rgba(0, 0, 0, 0.06)',
                  textAlign: 'left',
                  height: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    backgroundColor:
                      theme.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.08)'
                        : 'rgba(0, 0, 0, 0.04)',
                    boxShadow: theme.shadows[1],
                  },
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 400 }}>
                  &quot;What&apos;s our policy on remote work?&quot;
                </Typography>
              </Paper>
            </Grid>
            <Grid item xs={12} sm={6}>
              <Paper
                elevation={0}
                sx={{
                  p: 1.25,
                  borderRadius: 1.5,
                  backgroundColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.05)'
                      : 'rgba(0, 0, 0, 0.02)',
                  border: '1px solid',
                  borderColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.1)'
                      : 'rgba(0, 0, 0, 0.06)',
                  textAlign: 'left',
                  height: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    backgroundColor:
                      theme.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.08)'
                        : 'rgba(0, 0, 0, 0.04)',
                    boxShadow: theme.shadows[1],
                  },
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 400 }}>
                  &quot;Find the latest quarterly report&quot;
                </Typography>
              </Paper>
            </Grid>
            <Grid item xs={12}>
              <Paper
                elevation={0}
                sx={{
                  p: 1.25,
                  borderRadius: 1.5,
                  backgroundColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.05)'
                      : 'rgba(0, 0, 0, 0.02)',
                  border: '1px solid',
                  borderColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.1)'
                      : 'rgba(0, 0, 0, 0.06)',
                  textAlign: 'left',
                  display: 'flex',
                  alignItems: 'center',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    backgroundColor:
                      theme.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.08)'
                        : 'rgba(0, 0, 0, 0.04)',
                    boxShadow: theme.shadows[1],
                  },
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 400 }}>
                  &quot;Summarize our data security guidelines&quot;
                </Typography>
              </Paper>
            </Grid>
          </Grid>
        </Box>

        {/* Open source reference */}
        <Typography
          variant="caption"
          color="textSecondary"
          sx={{
            mt: 3,
            display: 'flex',
            alignItems: 'center',
            gap: 0.5,
            opacity: 0.65,
            transition: 'opacity 0.2s ease',
            '&:hover': {
              opacity: 0.9,
            },
          }}
        >
          <Icon icon={githubIcon} style={{ fontSize: '1rem' }} />
          Open Source Workplace AI
        </Typography>
      </Box>
    </Fade>
  );
};

export default WelcomeMessage;
