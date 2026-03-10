// SidebarKnowledgeSection Component
// Knowledge section rendering with apps and knowledge bases

import React from 'react';
import { Box, List, ListItem, Typography, useTheme, alpha } from '@mui/material';
import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { NodeTemplate, SidebarKnowledgeSectionProps } from './sidebar.types';
import { normalizeDisplayName } from '../../../utils/agent';

export const SidebarKnowledgeSection: React.FC<SidebarKnowledgeSectionProps> = ({
  groupedConnectorInstances,
  kbGroupNode,
  individualKBs,
  expandedApps,
  onAppToggle,
}) => {
  const theme = useTheme();

  return (
    <Box sx={{ pl: 0 }}>
      {/* Apps: Show active connector instances grouped by type */}
      {kbGroupNode && (
        <SidebarCategory
          groupLabel={kbGroupNode.label}
          groupIcon={kbGroupNode.icon}
          itemCount={Object.entries(groupedConnectorInstances).reduce((acc, [_, data]) => acc + data.instances.length, 0)}
          isExpanded={expandedApps.app || false}
          onToggle={() => onAppToggle('app')}
          dragType={kbGroupNode.type}
          borderColor={theme.palette.info.main}
        >
          <Box sx={{ pl: 0.5 }}>
            {Object.entries(groupedConnectorInstances).map(([connectorType, data]) => {
              const { instances, icon } = data;
              const isConnectorExpanded = expandedApps[`knowledge-connector-${connectorType}`];
              
              // Single instance: show directly without dropdown (no expand/collapse needed)
              if (instances.length === 1) {
                const connector = instances[0];
                const appDragType = `app-${connector.name.toLowerCase().replace(/\s+/g, '-')}`;
                
                return (
                  <ListItem
                    key={connectorType}
                    button
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData('application/reactflow', appDragType);
                      event.dataTransfer.setData('connectorId', connector._key || '');
                      event.dataTransfer.setData('connectorType', connector.type || '');
                      event.dataTransfer.setData('scope', connector.scope || 'personal');
                    }}
                    sx={{
                      py: 1,
                      px: 2,
                      pl: 5.5,
                      cursor: 'grab',
                      borderRadius: 1.5,
                      mx: 1,
                      mb: 0.5,
                      border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                      backgroundColor: 'transparent',
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.action.hover, 0.04),
                        borderColor: alpha(theme.palette.divider, 0.15),
                        transform: 'translateX(2px)',
                      },
                      '&:active': {
                        cursor: 'grabbing',
                        transform: 'scale(0.98)',
                      },
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
                      <img
                        src={icon}
                        alt={connector.name}
                        width={16}
                        height={16}
                        style={{ objectFit: 'contain' }}
                        onError={(e) => {
                          e.currentTarget.src = '/assets/icons/connectors/default.svg';
                        }}
                      />
                      <Typography
                        variant="body2"
                        sx={{
                          fontSize: '0.875rem',
                          color: theme.palette.text.primary,
                          fontWeight: 500,
                          flex: 1,
                        }}
                      >
                        {normalizeDisplayName(connector.name)}
                      </Typography>
                    </Box>
                  </ListItem>
                );
              }

              // Multiple instances: show as expandable group
              return (
                <SidebarCategory
                  key={connectorType}
                  groupLabel={connectorType}
                  groupIcon={icon}
                  itemCount={instances.length}
                  isExpanded={isConnectorExpanded}
                  onToggle={() => onAppToggle(`knowledge-connector-${connectorType}`)}
                  borderColor={theme.palette.info.main}
                >
                  <Box
                    sx={{
                      position: 'relative',
                      '&::before': {
                        content: '""',
                        position: 'absolute',
                        left: '32px',
                        top: 0,
                        bottom: 0,
                        width: '2px',
                        backgroundColor: alpha(theme.palette.info.main, 0.2),
                        borderRadius: '1px',
                      },
                    }}
                  >
                    <List dense sx={{ py: 0.5, pl: 2 }}>
                      {instances.map((connector: any) => {
                        const appDragType = `app-${connector.name.toLowerCase().replace(/\s+/g, '-')}`;
                        return (
                          <ListItem
                            key={connector._key}
                            button
                            draggable
                            onDragStart={(event) => {
                              event.dataTransfer.setData('application/reactflow', appDragType);
                              event.dataTransfer.setData('connectorId', connector._key || '');
                              event.dataTransfer.setData('connectorType', connector.type || '');
                              event.dataTransfer.setData('scope', connector.scope || 'personal');
                            }}
                            sx={{
                              py: 0.75,
                              px: 2,
                              pl: 3.5,
                              cursor: 'grab',
                              borderRadius: 1,
                              mx: 1.5,
                              my: 0.25,
                              border: `1px solid ${alpha(theme.palette.divider, 0.05)}`,
                              backgroundColor: 'transparent',
                              '&:hover': {
                                backgroundColor: alpha(theme.palette.action.hover, 0.04),
                                borderColor: alpha(theme.palette.divider, 0.1),
                              },
                              '&:active': {
                                cursor: 'grabbing',
                              },
                            }}
                          >
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
                              <img
                                src={icon}
                                alt={connector.name}
                                width={16}
                                height={16}
                                style={{ objectFit: 'contain' }}
                                onError={(e) => {
                                  e.currentTarget.src = '/assets/icons/connectors/default.svg';
                                }}
                              />
                              <Typography
                                variant="body2"
                                sx={{
                                  fontSize: '0.85rem',
                                  color: theme.palette.text.primary,
                                  fontWeight: 400,
                                  flex: 1,
                                  lineHeight: 1.4,
                                }}
                              >
                                {normalizeDisplayName(connector.name)}
                              </Typography>
                            </Box>
                          </ListItem>
                        );
                      })}
                    </List>
                  </Box>
                </SidebarCategory>
              );
            })}
          </Box>
        </SidebarCategory>
      )}

      {/* Collections group with dropdown */}
      {kbGroupNode && (
        <SidebarCategory
          groupLabel="Collections"
          groupIcon="/assets/icons/connectors/collections-gray.svg"
          itemCount={individualKBs.length}
          isExpanded={expandedApps['knowledge-bases'] || false}
          onToggle={() => onAppToggle('knowledge-bases')}
          dragType="kb-group"
          borderColor={theme.palette.warning.main}
        >
          <Box
            sx={{
              position: 'relative',
              '&::before': {
                content: '""',
                position: 'absolute',
                left: '32px',
                top: 0,
                bottom: 0,
                width: '2px',
                backgroundColor: alpha(theme.palette.warning.main, 0.2),
                borderRadius: '1px',
              },
            }}
          >
            <List dense sx={{ py: 0.5 }}>
              {individualKBs.map((kb) => (
                <SidebarNodeItem
                  key={kb.type}
                  template={kb}
                  isSubItem
                  sectionType="kbs"
                  itemIcon={kb.icon}
                />
              ))}
            </List>
          </Box>
        </SidebarCategory>
      )}
    </Box>
  );
};

