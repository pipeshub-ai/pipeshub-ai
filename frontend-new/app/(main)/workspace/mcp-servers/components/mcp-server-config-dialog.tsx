'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  Flex,
  Text,
  Button,
  TextField,
  Badge,
  Separator,
  IconButton,
  Spinner,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { McpServersApi } from '../api';
import { useMcpServersStore } from '../store';
import type { MCPServerInstance, MCPServerTemplate, MCPServerTool } from '../types';

// ============================================================================
// Helpers
// ============================================================================

function formatExpiryDate(timestamp?: number): string | null {
  if (!timestamp) return null;
  const date = new Date(timestamp * 1000);
  const now = Date.now();
  const diffMs = date.getTime() - now;

  if (diffMs < 0) return 'Expired';

  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 60) return `Expires in ${diffMins}m`;

  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `Expires in ${diffHours}h`;

  const diffDays = Math.floor(diffHours / 24);
  return `Expires in ${diffDays}d`;
}

// ============================================================================
// Sub-components
// ============================================================================

function SectionDivider({ label }: { label: string }) {
  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Separator size="4" />
      <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
        {label}
      </Text>
    </Flex>
  );
}

function FieldLabel({
  label,
  required,
}: {
  label: string;
  required?: boolean;
}) {
  return (
    <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
      {label}
      {required && (
        <span style={{ color: 'var(--red-9)', marginLeft: 2 }}>*</span>
      )}
    </Text>
  );
}

// ============================================================================
// API Token Auth Section
// ============================================================================

interface ApiTokenSectionProps {
  instanceId: string;
  isAuthenticated: boolean;
  onAuthSuccess: (updated: MCPServerInstance) => void;
}

function ApiTokenSection({
  instanceId,
  isAuthenticated,
  onAuthSuccess,
}: ApiTokenSectionProps) {
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const handleSave = async () => {
    if (!token.trim()) return;
    setIsSaving(true);
    try {
      await McpServersApi.authenticateInstance(instanceId, { apiToken: token });
      const { instance } = await McpServersApi.getInstance(instanceId);
      addToast({ variant: 'success', title: 'Authentication saved' });
      onAuthSuccess(instance);
      setToken('');
    } catch {
      addToast({ variant: 'error', title: 'Failed to save authentication' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!token.trim()) return;
    setIsSaving(true);
    try {
      await McpServersApi.updateCredentials(instanceId, { apiToken: token });
      const { instance } = await McpServersApi.getInstance(instanceId);
      addToast({ variant: 'success', title: 'Credentials updated' });
      onAuthSuccess(instance);
      setToken('');
    } catch {
      addToast({ variant: 'error', title: 'Failed to update credentials' });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Flex direction="column" gap="3">
      <Flex align="center" gap="2">
        <FieldLabel
          label={isAuthenticated ? 'Update API Token' : 'API Token'}
          required={!isAuthenticated}
        />
        {isAuthenticated && (
          <Badge color="green" variant="soft" size="1">
            Authenticated
          </Badge>
        )}
      </Flex>

      <Flex gap="2">
        <TextField.Root
          type={showToken ? 'text' : 'password'}
          placeholder="Enter API token..."
          value={token}
          onChange={(e) => setToken(e.target.value)}
          style={{ flex: 1 }}
        >
          <TextField.Slot side="right">
            <IconButton
              size="1"
              variant="ghost"
              color="gray"
              onClick={() => setShowToken((v) => !v)}
              type="button"
            >
              <MaterialIcon
                name={showToken ? 'visibility_off' : 'visibility'}
                size={14}
                color="var(--gray-9)"
              />
            </IconButton>
          </TextField.Slot>
        </TextField.Root>

        <Button
          size="2"
          variant="solid"
          onClick={isAuthenticated ? handleUpdate : handleSave}
          disabled={!token.trim() || isSaving}
        >
          {isSaving ? (
            <Spinner size="1" />
          ) : (
            <MaterialIcon
              name={isAuthenticated ? 'refresh' : 'key'}
              size={14}
              color="white"
            />
          )}
          {isAuthenticated ? 'Update' : 'Authenticate'}
        </Button>
      </Flex>
    </Flex>
  );
}

// ============================================================================
// OAuth Section
// ============================================================================

interface OAuthSectionProps {
  instanceId: string;
  instance: MCPServerInstance;
  onAuthSuccess: (updated: MCPServerInstance) => void;
}

function OAuthSection({ instanceId, instance, onAuthSuccess }: OAuthSectionProps) {
  const [isStarting, setIsStarting] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const expiryLabel = formatExpiryDate(instance.oauthExpiresAt);

  const handleStartOAuth = async () => {
    setIsStarting(true);
    try {
      const { authorizationUrl } = await McpServersApi.oauthAuthorize(instanceId);
      window.location.href = authorizationUrl;
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to start OAuth',
        description: 'Could not get authorization URL. Please try again.',
      });
      setIsStarting(false);
    }
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await McpServersApi.oauthRefresh(instanceId);
      const { instance: updated } = await McpServersApi.getInstance(instanceId);
      addToast({ variant: 'success', title: 'Token refreshed' });
      onAuthSuccess(updated);
    } catch {
      addToast({ variant: 'error', title: 'Failed to refresh token' });
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <Flex direction="column" gap="3">
      <Flex align="center" gap="2">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          OAuth Authentication
        </Text>
        {instance.isAuthenticated ? (
          <Badge color="green" variant="soft" size="1">
            Connected
          </Badge>
        ) : (
          <Badge color="orange" variant="soft" size="1">
            Not connected
          </Badge>
        )}
      </Flex>

      {instance.isAuthenticated && expiryLabel && (
        <Flex align="center" gap="1">
          <MaterialIcon name="schedule" size={12} color="var(--gray-9)" />
          <Text size="1" style={{ color: 'var(--gray-10)' }}>
            {expiryLabel}
          </Text>
        </Flex>
      )}

      <Flex gap="2" wrap="wrap">
        <Button
          size="2"
          variant={instance.isAuthenticated ? 'outline' : 'solid'}
          onClick={handleStartOAuth}
          disabled={isStarting}
        >
          {isStarting ? <Spinner size="1" /> : <MaterialIcon name="open_in_new" size={14} color="var(--gray-11)" />}
          {instance.isAuthenticated ? 'Reconnect' : 'Authenticate with OAuth'}
        </Button>

        {instance.isAuthenticated && (
          <Button
            size="2"
            variant="outline"
            color="gray"
            onClick={handleRefresh}
            disabled={isRefreshing}
          >
            {isRefreshing ? <Spinner size="1" /> : <MaterialIcon name="refresh" size={14} color="var(--gray-11)" />}
            Refresh Token
          </Button>
        )}
      </Flex>
    </Flex>
  );
}

// ============================================================================
// OAuth Client Config Section (admin)
// ============================================================================

interface OAuthClientConfigSectionProps {
  instanceId: string;
}

function OAuthClientConfigSection({ instanceId }: OAuthClientConfigSectionProps) {
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const addToast = useToastStore((s) => s.addToast);

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const { config } = await McpServersApi.getInstanceOauthConfig(instanceId);
        if (config) {
          setClientId((config.clientId as string) ?? '');
        }
      } catch {
        // not configured yet
      } finally {
        setIsLoading(false);
      }
    };
    loadConfig();
  }, [instanceId]);

  const handleSave = async () => {
    if (!clientId.trim()) return;
    setIsSaving(true);
    try {
      await McpServersApi.setInstanceOauthConfig(instanceId, {
        clientId,
        clientSecret: clientSecret || undefined,
      });
      addToast({ variant: 'success', title: 'OAuth client config saved' });
      setClientSecret('');
    } catch {
      addToast({ variant: 'error', title: 'Failed to save OAuth client config' });
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ padding: '12px 0' }}>
        <Spinner size="2" />
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap="3">
      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
        OAuth Client Configuration
      </Text>
      <Text size="1" style={{ color: 'var(--gray-10)' }}>
        Admin-only: configure the OAuth client credentials for this server instance.
      </Text>

      <Flex direction="column" gap="2">
        <FieldLabel label="Client ID" required />
        <TextField.Root
          placeholder="Enter client ID..."
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
        />
      </Flex>

      <Flex direction="column" gap="2">
        <FieldLabel label="Client Secret" />
        <TextField.Root
          type={showSecret ? 'text' : 'password'}
          placeholder="Enter client secret (leave blank to keep existing)..."
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
        >
          <TextField.Slot side="right">
            <IconButton
              size="1"
              variant="ghost"
              color="gray"
              onClick={() => setShowSecret((v) => !v)}
              type="button"
            >
              <MaterialIcon
                name={showSecret ? 'visibility_off' : 'visibility'}
                size={14}
                color="var(--gray-9)"
              />
            </IconButton>
          </TextField.Slot>
        </TextField.Root>
      </Flex>

      <Button
        size="2"
        variant="solid"
        onClick={handleSave}
        disabled={!clientId.trim() || isSaving}
        style={{ alignSelf: 'flex-start' }}
      >
        {isSaving ? <Spinner size="1" /> : <MaterialIcon name="save" size={14} color="white" />}
        Save Client Config
      </Button>
    </Flex>
  );
}

// ============================================================================
// Tool Discovery Section
// ============================================================================

interface ToolDiscoverySectionProps {
  instanceId: string;
  tools: MCPServerTool[];
  isDiscovering: boolean;
  onDiscover: () => void;
}

function ToolDiscoverySection({
  instanceId: _instanceId,
  tools,
  isDiscovering,
  onDiscover,
}: ToolDiscoverySectionProps) {
  return (
    <Flex direction="column" gap="3">
      <Flex align="center" justify="between">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          Available Tools
        </Text>
        <Button
          size="1"
          variant="outline"
          color="gray"
          onClick={onDiscover}
          disabled={isDiscovering}
        >
          {isDiscovering ? <Spinner size="1" /> : <MaterialIcon name="refresh" size={12} color="var(--gray-11)" />}
          Discover
        </Button>
      </Flex>

      {tools.length === 0 ? (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {isDiscovering
            ? 'Discovering tools...'
            : 'Click Discover to load available tools from this server.'}
        </Text>
      ) : (
        <Flex direction="column" gap="2">
          {tools.map((tool) => (
            <Flex
              key={tool.namespacedName}
              direction="column"
              gap="1"
              style={{
                padding: '8px 10px',
                borderRadius: 'var(--radius-1)',
                backgroundColor: 'var(--gray-a2)',
              }}
            >
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {tool.name}
              </Text>
              {tool.description && (
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  {tool.description}
                </Text>
              )}
            </Flex>
          ))}
        </Flex>
      )}
    </Flex>
  );
}

// ============================================================================
// Create Form
// ============================================================================

interface CreateFormProps {
  template: MCPServerTemplate;
  onCreated: (instance: MCPServerInstance) => void;
  onCancel: () => void;
}

function CreateForm({ template, onCreated, onCancel }: CreateFormProps) {
  const [instanceName, setInstanceName] = useState(template.displayName);
  const [nameError, setNameError] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [apiToken, setApiToken] = useState('');
  const [showApiToken, setShowApiToken] = useState(false);
  const [copiedRedirectUri, setCopiedRedirectUri] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const isOAuth =
    template.authMode === 'oauth' ||
    template.supportedAuthTypes?.includes('OAUTH') ||
    template.supportedAuthTypes?.includes('oauth2');

  const isApiToken = template.authMode === 'api_token';

  const redirectUri = (() => {
    const serverType = template.typeId || '';
    const path =
      template.redirectUri ||
      (serverType ? `mcp-servers/oauth/callback/${serverType}` : '');
    if (!path) return '';
    return `${window.location.origin}/${path}`;
  })();

  const handleCopyRedirectUri = () => {
    navigator.clipboard.writeText(redirectUri);
    setCopiedRedirectUri(true);
    setTimeout(() => setCopiedRedirectUri(false), 2000);
  };

  const handleCreate = async () => {
    if (!instanceName.trim()) {
      setNameError('Instance name is required');
      return;
    }
    if (isOAuth && !clientId.trim()) {
      addToast({ variant: 'error', title: 'OAuth Client ID is required' });
      return;
    }
    setIsCreating(true);
    try {
      const body: Parameters<typeof McpServersApi.createInstance>[0] = {
        instanceName: instanceName.trim(),
        serverType: template.typeId,
        displayName: template.displayName,
        description: template.description,
        transport: template.transport,
        command: template.command,
        args: template.defaultArgs,
        url: template.url,
        authMode: template.authMode,
        supportedAuthTypes: template.supportedAuthTypes,
        requiredEnv: template.requiredEnv,
        iconPath: template.iconPath,
      };

      if (isOAuth && clientId.trim()) {
        body.clientId = clientId.trim();
        body.clientSecret = clientSecret.trim() || undefined;
      }

      if (isApiToken && apiToken.trim()) {
        body.apiToken = apiToken.trim();
      }

      const { instance } = await McpServersApi.createInstance(body);
      console.log('instance', instance);

      if (isApiToken && apiToken.trim()) {
        try {
          await McpServersApi.authenticateInstance(instance.instanceId, {
            apiToken: apiToken.trim(),
          });
        } catch {
          addToast({
            variant: 'error',
            title: 'Server added but authentication failed',
            description: 'You can retry authentication from My Servers.',
          });
          onCreated(instance);
          return;
        }
      }

      addToast({
        variant: 'success',
        title: 'MCP server added',
        description: `${instance.displayName || instance.instanceName} has been added.`,
      });
      onCreated(instance);
    } catch {
      addToast({ variant: 'error', title: 'Failed to add MCP server' });
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <Flex direction="column" gap="4">
      {/* Instance Name */}
      <Flex direction="column" gap="2">
        <FieldLabel label="Instance Name" required />
        <TextField.Root
          placeholder={`e.g. ${template.displayName} - Production`}
          value={instanceName}
          onChange={(e) => {
            setInstanceName(e.target.value);
            setNameError('');
          }}
        />
        {nameError && (
          <Text size="1" style={{ color: 'var(--red-9)' }}>
            {nameError}
          </Text>
        )}
      </Flex>

      {/* OAuth Client Credentials */}
      {isOAuth && (
        <>
          <SectionDivider label="OAuth App Credentials" />
          <Text size="1" style={{ color: 'var(--gray-10)', marginTop: -8 }}>
            Create an OAuth app with your provider and enter the credentials.
            All users will authenticate through this OAuth app.
          </Text>

          {redirectUri && (
            <Flex direction="column" gap="1">
              <Text size="1" weight="medium" style={{ color: 'var(--gray-11)' }}>
                Redirect URI (copy to your OAuth app config)
              </Text>
              <Flex
                align="center"
                gap="2"
                style={{
                  padding: '6px 10px',
                  borderRadius: 'var(--radius-2)',
                  background: 'var(--gray-a3)',
                }}
              >
                <Text
                  size="1"
                  style={{
                    fontFamily: 'monospace',
                    flex: 1,
                    wordBreak: 'break-all',
                    color: 'var(--gray-12)',
                  }}
                >
                  {redirectUri}
                </Text>
                <IconButton
                  size="1"
                  variant="ghost"
                  color={copiedRedirectUri ? 'green' : 'gray'}
                  onClick={handleCopyRedirectUri}
                >
                  <MaterialIcon
                    name={copiedRedirectUri ? 'check' : 'content_copy'}
                    size={14}
                    color={copiedRedirectUri ? 'var(--green-9)' : 'var(--gray-9)'}
                  />
                </IconButton>
              </Flex>
            </Flex>
          )}

          <Flex direction="column" gap="2">
            <FieldLabel label="Client ID" required />
            <TextField.Root
              placeholder="Enter your OAuth Client ID"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
            />
          </Flex>

          <Flex direction="column" gap="2">
            <FieldLabel label="Client Secret" />
            <TextField.Root
              type="password"
              placeholder="Enter your OAuth Client Secret"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
            />
          </Flex>
        </>
      )}

      {/* API Token */}
      {isApiToken && (
        <>
          <SectionDivider label="API Token" />
          <Text size="1" style={{ color: 'var(--gray-10)', marginTop: -8 }}>
            Provide the API token for this server. You can also configure this later from My Servers.
          </Text>

          <Flex direction="column" gap="2">
            <Flex gap="2">
              <TextField.Root
                type={showApiToken ? 'text' : 'password'}
                placeholder="Enter API token..."
                value={apiToken}
                onChange={(e) => setApiToken(e.target.value)}
                style={{ flex: 1 }}
              >
                <TextField.Slot side="right">
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="gray"
                    onClick={() => setShowApiToken((v) => !v)}
                    type="button"
                  >
                    <MaterialIcon
                      name={showApiToken ? 'visibility_off' : 'visibility'}
                      size={14}
                      color="var(--gray-9)"
                    />
                  </IconButton>
                </TextField.Slot>
              </TextField.Root>
            </Flex>
          </Flex>
        </>
      )}

      {/* Tags */}
      {template.tags && template.tags.length > 0 && (
        <Flex gap="2" align="center" wrap="wrap">
          {template.tags.map((tag) => (
            <Badge key={tag} variant="outline" color="gray" size="1">
              {tag}
            </Badge>
          ))}
        </Flex>
      )}

      <Flex gap="2" justify="end" style={{ marginTop: 8 }}>
        <Button size="2" variant="outline" color="gray" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="2"
          variant="solid"
          onClick={handleCreate}
          disabled={isCreating}
        >
          {isCreating ? <Spinner size="1" /> : null}
          Add Server
        </Button>
      </Flex>
    </Flex>
  );
}

// ============================================================================
// Main Dialog
// ============================================================================

interface McpServerConfigDialogProps {
  open: boolean;
  instance?: MCPServerInstance;
  template?: MCPServerTemplate;
  onClose: () => void;
  onRefresh: () => void;
}

export function McpServerConfigDialog({
  open,
  instance,
  template,
  onClose,
  onRefresh,
}: McpServerConfigDialogProps) {
  const isAdmin = useUserStore(selectIsAdmin);
  const addToast = useToastStore((s) => s.addToast);
  const { updateMyServer, removeMyServer } = useMcpServersStore();

  const activeInstance = instance;

  const {
    discoveredTools,
    isDiscoveringTools,
    setDiscoveredTools,
    setIsDiscoveringTools,
  } = useMcpServersStore();

  const [isDeleting, setIsDeleting] = useState(false);

  const isOAuth = (activeInstance?.authMode ?? template?.authMode) === 'oauth';
  const isApiToken =
    (activeInstance?.authMode ?? template?.authMode) === 'api_token';

  const handleAuthSuccess = useCallback(
    (updated: MCPServerInstance) => {
      updateMyServer(updated);
    },
    [updateMyServer]
  );

  const handleCreated = (_newInstance: MCPServerInstance) => {
    onRefresh();
    onClose();
  };

  const handleDelete = async () => {
    if (!activeInstance) return;
    setIsDeleting(true);
    try {
      await McpServersApi.deleteInstance(activeInstance.instanceId);
      // removeMyServer removes the card from the list in-place — no refetch needed.
      removeMyServer(activeInstance.instanceId);
      addToast({ variant: 'success', title: 'MCP server removed' });
      onClose();
    } catch {
      addToast({ variant: 'error', title: 'Failed to remove MCP server' });
    } finally {
      setIsDeleting(false);
    }
  };

  const handleDiscoverTools = async () => {
    if (!activeInstance) return;
    setIsDiscoveringTools(true);
    try {
      const { tools } = await McpServersApi.discoverInstanceTools(
        activeInstance.instanceId
      );
      setDiscoveredTools(tools);
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to discover tools',
        description:
          'Make sure the server is authenticated and reachable.',
      });
    } finally {
      setIsDiscoveringTools(false);
    }
  };

  const handleClose = () => {
    onClose();
  };

  const dialogTitle = activeInstance
    ? activeInstance.displayName || activeInstance.instanceName
    : template?.displayName ?? 'Add MCP Server';

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => !o && handleClose()}
      title={dialogTitle}
      icon={<MaterialIcon name="dns" size={16} color="var(--slate-11)" />}
      hideFooter
    >
      {/* ── Description ── */}
      {(activeInstance?.description ?? template?.description) && (
        <Text size="2" style={{ color: 'var(--gray-10)', display: 'block', marginBottom: 16 }}>
          {activeInstance?.description ?? template?.description}
        </Text>
      )}

      {/* ── Create form (template mode, before first save) ── */}
      {!activeInstance && template && (
        <CreateForm
          template={template}
          onCreated={handleCreated}
          onCancel={handleClose}
        />
      )}

      {/* ── Manage existing instance ── */}
      {activeInstance && (
        <Flex direction="column" gap="5">
          {/* Auth section */}
          {isApiToken && (
            <>
              <SectionDivider label="Authentication" />
              <ApiTokenSection
                instanceId={activeInstance.instanceId}
                isAuthenticated={activeInstance.isAuthenticated ?? false}
                onAuthSuccess={handleAuthSuccess}
              />
            </>
          )}

          {isOAuth && (
            <>
              <SectionDivider label="Authentication" />
              <OAuthSection
                instanceId={activeInstance.instanceId}
                instance={activeInstance}
                onAuthSuccess={handleAuthSuccess}
              />

              {/* Admin OAuth client config */}
              {isAdmin && activeInstance.hasOAuthClientConfig !== undefined && (
                <>
                  <SectionDivider label="OAuth Client Config (Admin)" />
                  <OAuthClientConfigSection
                    instanceId={activeInstance.instanceId}
                  />
                </>
              )}
            </>
          )}

          {/* Tool discovery */}
          {activeInstance.isAuthenticated && (
            <>
              <SectionDivider label="Tools" />
              <ToolDiscoverySection
                instanceId={activeInstance.instanceId}
                tools={discoveredTools}
                isDiscovering={isDiscoveringTools}
                onDiscover={handleDiscoverTools}
              />
            </>
          )}

          {/* Admin delete */}
          {isAdmin && (
            <>
              <SectionDivider label="Danger Zone" />
              <Flex align="center" justify="between">
                <Flex direction="column" gap="1">
                  <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                    Remove this server
                  </Text>
                  <Text size="1" style={{ color: 'var(--gray-10)' }}>
                    This will remove the server instance from the workspace.
                  </Text>
                </Flex>
                <Button
                  size="2"
                  variant="outline"
                  color="red"
                  onClick={handleDelete}
                  disabled={isDeleting}
                >
                  {isDeleting ? (
                    <Spinner size="1" />
                  ) : (
                    <MaterialIcon name="delete" size={14} color="var(--red-9)" />
                  )}
                  Remove
                </Button>
              </Flex>
            </>
          )}
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}
