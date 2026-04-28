'use client';

import { useContext, useState } from 'react';
import {
  Flex,
  Text,
  Button,
  TextField,
  Select,
  Separator,
  IconButton,
  Spinner,
  TextArea,
  Switch,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  WorkspaceRightPanel,
  WorkspaceRightPanelBodyPortalContext,
  WORKSPACE_DRAWER_POPPER_Z_INDEX,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import { McpServersApi } from '../api';
import type { MCPServerInstance } from '../types';

// ============================================================================
// Constants
// ============================================================================

const TRANSPORT_OPTIONS = [
  { value: 'stdio', label: 'Standard I/O (stdio)', description: 'Local process — provide a command and arguments' },
  { value: 'streamable_http', label: 'Streamable HTTP', description: 'Remote server — provide a URL endpoint' },
  { value: 'sse', label: 'Server-Sent Events (SSE)', description: 'Remote server via SSE — provide a URL endpoint' },
];

const AUTH_MODE_OPTIONS = [
  { value: 'none', label: 'None', description: 'No authentication required' },
  { value: 'api_token', label: 'API Token', description: 'Token passed as environment variable or header' },
  { value: 'oauth', label: 'OAuth 2.0', description: 'OAuth authorization code flow' },
  { value: 'headers', label: 'Custom Headers', description: 'Static authorization headers' },
];

// ============================================================================
// Sub-components
// ============================================================================

function FieldLabel({ label, required }: { label: string; required?: boolean }) {
  return (
    <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
      {label}
      {required && <span style={{ color: 'var(--red-9)', marginLeft: 2 }}>*</span>}
    </Text>
  );
}

function FieldHint({ children }: { children: React.ReactNode }) {
  return (
    <Text size="1" style={{ color: 'var(--gray-9)' }}>
      {children}
    </Text>
  );
}

// ============================================================================
// Props
// ============================================================================

interface CustomMcpServerDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (instance: MCPServerInstance) => void;
}

// ============================================================================
// Component
// ============================================================================

export function CustomMcpServerDialog({
  open,
  onClose,
  onCreated,
}: CustomMcpServerDialogProps) {
  const addToast = useToastStore((s) => s.addToast);
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);

  const [instanceName, setInstanceName] = useState('');
  const [description, setDescription] = useState('');
  const [transport, setTransport] = useState('stdio');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [url, setUrl] = useState('');
  const [envVars, setEnvVars] = useState('');
  const [authMode, setAuthMode] = useState('none');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [authorizationUrl, setAuthorizationUrl] = useState('');
  const [tokenUrl, setTokenUrl] = useState('');
  const [scopes, setScopes] = useState('');
  const [headerKey, setHeaderKey] = useState('Authorization');
  const [headerValue, setHeaderValue] = useState('');
  const [useAdminAuth, setUseAdminAuth] = useState(false);
  const [adminApiToken, setAdminApiToken] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedRedirectUri, setCopiedRedirectUri] = useState(false);

  const redirectUri = typeof window !== 'undefined'
    ? `${window.location.origin}/mcp-servers/oauth/callback/custom`
    : '';

  const handleCopyRedirectUri = () => {
    navigator.clipboard.writeText(redirectUri);
    setCopiedRedirectUri(true);
    setTimeout(() => setCopiedRedirectUri(false), 2000);
  };

  const isHttpTransport = transport === 'streamable_http' || transport === 'sse';
  const supportsAdminAuth = authMode === 'api_token' || authMode === 'headers';

  const handleCreate = async () => {
    setError(null);

    if (!instanceName.trim()) {
      setError('Instance name is required');
      return;
    }
    if (transport === 'stdio' && !command.trim()) {
      setError('Command is required for stdio transport');
      return;
    }
    if (isHttpTransport && !url.trim()) {
      setError('URL is required for HTTP transport');
      return;
    }
    if (useAdminAuth && supportsAdminAuth && !adminApiToken.trim()) {
      setError('API token is required when "Use shared admin token" is enabled');
      return;
    }

    setIsCreating(true);
    try {
      const parsedArgs = args
        .split(/\s+/)
        .map((a) => a.trim())
        .filter(Boolean);

      const requiredEnv: string[] = [];
      if (envVars.trim()) {
        envVars.split(',').forEach((v) => {
          const trimmed = v.trim();
          if (trimmed) requiredEnv.push(trimmed);
        });
      }

      const body: Parameters<typeof McpServersApi.createInstance>[0] = {
        instanceName: instanceName.trim(),
        serverType: 'custom',
        displayName: instanceName.trim(),
        description: description.trim() || undefined,
        transport,
        authMode,
        supportedAuthTypes: authMode === 'none' ? [] : [authMode.toUpperCase()],
        useAdminAuth: supportsAdminAuth ? useAdminAuth : false,
      };

      if (transport === 'stdio') {
        body.command = command.trim();
        body.args = parsedArgs;
        body.requiredEnv = requiredEnv;
      } else {
        body.url = url.trim();
      }

      if (authMode === 'oauth') {
        if (clientId.trim()) {
          body.clientId = clientId.trim();
          body.clientSecret = clientSecret.trim() || undefined;
        }
        if (authorizationUrl.trim()) body.authorizationUrl = authorizationUrl.trim();
        if (tokenUrl.trim()) body.tokenUrl = tokenUrl.trim();
        if (scopes.trim()) {
          body.scopes = scopes.split(',').map((s) => s.trim()).filter(Boolean);
        }
      }

      if (authMode === 'headers') {
        body.headerName = headerKey.trim() || 'Authorization';
      }

      if (supportsAdminAuth && useAdminAuth && adminApiToken.trim()) {
        body.apiToken = adminApiToken.trim();
      }

      const result = await McpServersApi.createInstance(body);

      addToast({ variant: 'success', title: 'Custom MCP server created' });
      onCreated(result.instance);
      resetAndClose();
    } catch {
      addToast({ variant: 'error', title: 'Failed to create server' });
    } finally {
      setIsCreating(false);
    }
  };

  const resetAndClose = () => {
    setInstanceName('');
    setDescription('');
    setTransport('stdio');
    setCommand('');
    setArgs('');
    setUrl('');
    setEnvVars('');
    setAuthMode('none');
    setClientId('');
    setClientSecret('');
    setAuthorizationUrl('');
    setTokenUrl('');
    setScopes('');
    setHeaderKey('Authorization');
    setHeaderValue('');
    setUseAdminAuth(false);
    setAdminApiToken('');
    setError(null);
    onClose();
  };

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => !o && resetAndClose()}
      title="Add Custom MCP Server"
      icon={<MaterialIcon name="add_circle" size={16} color="var(--slate-11)" />}
      hideFooter
    >
      <Text size="2" style={{ color: 'var(--gray-10)', display: 'block', marginBottom: 16 }}>
        Connect any MCP-compatible server
      </Text>

      <Flex direction="column" gap="4">
        {/* Error */}
        {error && (
          <Flex
            align="center"
            gap="2"
            style={{
              padding: '8px 12px',
              borderRadius: 'var(--radius-2)',
              background: 'var(--red-a3)',
              border: '1px solid var(--red-a6)',
            }}
          >
            <MaterialIcon name="error" size={16} color="var(--red-9)" />
            <Text size="2" style={{ color: 'var(--red-11)', flex: 1 }}>
              {error}
            </Text>
            <IconButton size="1" variant="ghost" color="red" onClick={() => setError(null)}>
              <MaterialIcon name="close" size={14} color="var(--red-9)" />
            </IconButton>
          </Flex>
        )}

        {/* Server Name */}
        <Flex direction="column" gap="2">
          <FieldLabel label="Server Name" required />
          <TextField.Root
            placeholder="e.g. My GitHub MCP Server"
            value={instanceName}
            onChange={(e) => setInstanceName(e.target.value)}
          />
        </Flex>

        {/* Description */}
        <Flex direction="column" gap="2">
          <FieldLabel label="Description" />
          <TextArea
            placeholder="Optional description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            style={{ resize: 'vertical' }}
          />
        </Flex>

        <Separator size="4" />

        {/* Transport */}
        <Flex direction="column" gap="2">
          <FieldLabel label="Transport" />
          <Select.Root value={transport} onValueChange={setTransport}>
            <Select.Trigger style={{ width: '100%' }} />
            <Select.Content
              position="popper"
              container={panelBodyPortal ?? undefined}
              style={{ zIndex: WORKSPACE_DRAWER_POPPER_Z_INDEX }}
            >
              {TRANSPORT_OPTIONS.map((opt) => (
                <Select.Item key={opt.value} value={opt.value}>
                  {opt.label}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
          <FieldHint>
            {TRANSPORT_OPTIONS.find((o) => o.value === transport)?.description}
          </FieldHint>
        </Flex>

        {/* stdio fields */}
        {transport === 'stdio' && (
          <>
            <Flex direction="column" gap="2">
              <FieldLabel label="Command" required />
              <TextField.Root
                placeholder="e.g. npx, uvx, docker"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
              <FieldHint>The executable to run</FieldHint>
            </Flex>

            <Flex direction="column" gap="2">
              <FieldLabel label="Arguments" />
              <TextField.Root
                placeholder="e.g. -y @modelcontextprotocol/server-github"
                value={args}
                onChange={(e) => setArgs(e.target.value)}
              />
              <FieldHint>Space-separated arguments passed to the command</FieldHint>
            </Flex>

            <Flex direction="column" gap="2">
              <FieldLabel label="Required Environment Variables" />
              <TextField.Root
                placeholder="e.g. GITHUB_TOKEN, CUSTOM_API_KEY"
                value={envVars}
                onChange={(e) => setEnvVars(e.target.value)}
              />
              <FieldHint>Comma-separated env var names the server needs</FieldHint>
            </Flex>
          </>
        )}

        {/* HTTP/SSE fields */}
        {isHttpTransport && (
          <Flex direction="column" gap="2">
            <FieldLabel label="Server URL" required />
            <TextField.Root
              placeholder="e.g. https://mcp.example.com/v1/mcp"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <FieldHint>The HTTP endpoint for the MCP server</FieldHint>
          </Flex>
        )}

        <Separator size="4" />

        {/* Authentication */}
        <Flex direction="column" gap="2">
          <FieldLabel label="Authentication" />
          <Select.Root value={authMode} onValueChange={setAuthMode}>
            <Select.Trigger style={{ width: '100%' }} />
            <Select.Content
              position="popper"
              container={panelBodyPortal ?? undefined}
              style={{ zIndex: WORKSPACE_DRAWER_POPPER_Z_INDEX }}
            >
              {AUTH_MODE_OPTIONS.map((opt) => (
                <Select.Item key={opt.value} value={opt.value}>
                  {opt.label}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
          <FieldHint>
            {AUTH_MODE_OPTIONS.find((o) => o.value === authMode)?.description}
          </FieldHint>
        </Flex>

        {/* useAdminAuth toggle — only for api_token / headers modes */}
        {supportsAdminAuth && (
          <Flex
            direction="column"
            gap="3"
            style={{
              padding: 12,
              borderRadius: 'var(--radius-2)',
              border: '1px solid var(--gray-a5)',
              background: 'var(--gray-a2)',
            }}
          >
            <Flex align="center" justify="between" gap="3">
              <Flex direction="column" gap="1" style={{ flex: 1 }}>
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                  Use shared admin token
                </Text>
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  When enabled, you provide a service token now and all users automatically
                  authenticate with it. When disabled, each user must supply their own token.
                </Text>
              </Flex>
              <Switch
                checked={useAdminAuth}
                onCheckedChange={setUseAdminAuth}
                size="2"
              />
            </Flex>

            {useAdminAuth && (
              <Flex direction="column" gap="2">
                <FieldLabel
                  label={authMode === 'headers' ? 'Shared Header Value' : 'Service API Token'}
                  required
                />
                <TextField.Root
                  type="password"
                  placeholder={
                    authMode === 'headers'
                      ? `Enter the shared value for the ${headerKey || 'Authorization'} header. e.g. Bearer sk-...`
                      : 'Enter the shared API token'
                  }
                  value={adminApiToken}
                  onChange={(e) => setAdminApiToken(e.target.value)}
                />
                <FieldHint>
                  This value will be used for all users. It is stored securely and never
                  exposed in the UI.
                </FieldHint>
              </Flex>
            )}
          </Flex>
        )}

        {/* OAuth fields */}
        {authMode === 'oauth' && (
          <Flex
            direction="column"
            gap="3"
            style={{
              padding: 12,
              borderRadius: 'var(--radius-2)',
              border: '1px solid var(--gray-a5)',
              background: 'var(--gray-a2)',
            }}
          >
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              OAuth App Credentials
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              If your server supports Dynamic Client Registration (DCR), you can leave the
              fields below blank — credentials will be auto-discovered when you authenticate.
              Otherwise, create an OAuth app with your provider and enter the credentials below.
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
              <FieldLabel label="Client ID" />
              <TextField.Root
                placeholder="Enter your OAuth Client ID (optional for DCR servers)"
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
            <Flex direction="column" gap="2">
              <FieldLabel label="Authorization URL" />
              <TextField.Root
                placeholder="e.g. https://github.com/login/oauth/authorize"
                value={authorizationUrl}
                onChange={(e) => setAuthorizationUrl(e.target.value)}
              />
              <FieldHint>The URL where users are sent to authorize your app</FieldHint>
            </Flex>
            <Flex direction="column" gap="2">
              <FieldLabel label="Token URL" />
              <TextField.Root
                placeholder="e.g. https://github.com/login/oauth/access_token"
                value={tokenUrl}
                onChange={(e) => setTokenUrl(e.target.value)}
              />
              <FieldHint>The URL used to exchange the authorization code for tokens</FieldHint>
            </Flex>
            <Flex direction="column" gap="2">
              <FieldLabel label="Scopes" />
              <TextField.Root
                placeholder="e.g. repo, read:user"
                value={scopes}
                onChange={(e) => setScopes(e.target.value)}
              />
              <FieldHint>Comma-separated OAuth scopes to request</FieldHint>
            </Flex>
            <FieldHint>
              Users will authenticate via OAuth after the server is created.
            </FieldHint>
          </Flex>
        )}

        {/* Headers auth fields */}
        {authMode === 'headers' && (
          <Flex
            direction="column"
            gap="3"
            style={{
              padding: 12,
              borderRadius: 'var(--radius-2)',
              border: '1px solid var(--gray-a5)',
              background: 'var(--gray-a2)',
            }}
          >
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Authorization Header
            </Text>
            <Flex direction="column" gap="2">
              <FieldLabel label="Header Name" />
              <TextField.Root
                placeholder="Authorization"
                value={headerKey}
                onChange={(e) => setHeaderKey(e.target.value)}
              />
            </Flex>
            {!useAdminAuth && (
              <Flex direction="column" gap="2">
                <FieldLabel label="Header Value" />
                <TextField.Root
                  type="password"
                  placeholder="Bearer sk-..."
                  value={headerValue}
                  onChange={(e) => setHeaderValue(e.target.value)}
                />
                <FieldHint>Optional — you can configure this later</FieldHint>
              </Flex>
            )}
          </Flex>
        )}

        {/* Info hint for api_token + stdio when useAdminAuth is off */}
        {authMode === 'api_token' && transport === 'stdio' && !useAdminAuth && (
          <Flex
            align="center"
            gap="2"
            style={{
              padding: '8px 12px',
              borderRadius: 'var(--radius-2)',
              background: 'var(--blue-a3)',
              border: '1px solid var(--blue-a6)',
            }}
          >
            <MaterialIcon name="info" size={16} color="var(--blue-9)" />
            <Text size="1" style={{ color: 'var(--blue-11)' }}>
              Each user will be prompted to provide their own API token when they authenticate.
            </Text>
          </Flex>
        )}
      </Flex>

      {/* Footer */}
      <Flex gap="2" justify="end" style={{ marginTop: 20 }}>
        <Button size="2" variant="outline" color="gray" onClick={resetAndClose}>
          Cancel
        </Button>
        <Button
          size="2"
          variant="solid"
          onClick={handleCreate}
          disabled={isCreating}
        >
          {isCreating ? <Spinner size="1" /> : <MaterialIcon name="add_circle" size={14} />}
          Create Server
        </Button>
      </Flex>
    </WorkspaceRightPanel>
  );
}
