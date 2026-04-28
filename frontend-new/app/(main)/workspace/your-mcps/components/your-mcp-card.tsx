'use client';

import { useState } from 'react';
import { Flex, Text, Badge, Button, Spinner, TextField, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import type { MCPServerInstance } from '@/app/(main)/workspace/mcp-servers/types';

// ============================================================================
// Helpers
// ============================================================================

function authModeLabel(authMode: string): string {
  switch (authMode) {
    case 'oauth':
      return 'OAuth';
    case 'api_token':
      return 'API Token';
    case 'headers':
      return 'Headers';
    default:
      return 'None';
  }
}

function authModeColor(authMode: string): 'blue' | 'purple' | 'orange' | 'gray' {
  switch (authMode) {
    case 'oauth':
      return 'blue';
    case 'api_token':
      return 'purple';
    case 'headers':
      return 'orange';
    default:
      return 'gray';
  }
}

// ============================================================================
// Props
// ============================================================================

interface YourMcpCardProps {
  server: MCPServerInstance;
  onRefresh: () => void;
  onOAuthSignIn: (server: MCPServerInstance) => void;
  onNotify: (message: string, variant?: 'success' | 'error') => void;
}

// ============================================================================
// YourMcpCard
// ============================================================================

export function YourMcpCard({ server, onRefresh, onOAuthSignIn, onNotify }: YourMcpCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);
  // Personal token state (used when useAdminAuth is false)
  const [personalToken, setPersonalToken] = useState('');
  const [showPersonalToken, setShowPersonalToken] = useState(false);
  // For headers auth mode: the name of the header to send (e.g. "Authorization")
  const [personalHeaderName, setPersonalHeaderName] = useState(
    server.defaultHeaderName || 'Authorization'
  );

  const isAuthenticated = server.isAuthenticated ?? false;
  const authMode = server.authMode || 'none';
  const useAdminAuth = server.useAdminAuth ?? false;
  const toolCount = server.toolCount ?? server.tools?.length ?? 0;

  // When useAdminAuth is true the platform resolves credentials from the admin's
  // record at read-time — no per-user copy or explicit authenticate step needed.
  const isAdminManaged = useAdminAuth && (authMode === 'api_token' || authMode === 'headers');

  const handlePersonalTokenAuthenticate = async () => {
    if (!personalToken.trim()) return;
    setIsAuthenticating(true);
    try {
      const authPayload =
        authMode === 'headers'
          ? {
              headerName: personalHeaderName.trim() || 'Authorization',
              headerValue: personalToken.trim(),
            }
          : { apiToken: personalToken.trim() };
      await McpServersApi.authenticateInstance(server.instanceId, authPayload);
      onNotify(`${server.displayName || server.instanceName} authenticated successfully.`, 'success');
      setPersonalToken('');
      onRefresh();
    } catch {
      onNotify(`Failed to authenticate ${server.displayName || server.instanceName}. Please check your credentials.`, 'error');
    } finally {
      setIsAuthenticating(false);
    }
  };

  const handleRemoveCredentials = async () => {
    setIsRemoving(true);
    try {
      await McpServersApi.removeCredentials(server.instanceId);
      onNotify(`Credentials removed for ${server.displayName || server.instanceName}.`, 'success');
      onRefresh();
    } catch {
      onNotify(`Failed to remove credentials for ${server.displayName || server.instanceName}.`, 'error');
    } finally {
      setIsRemoving(false);
    }
  };

  const handleOAuthSignIn = () => {
    onOAuthSignIn(server);
  };

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 12,
        gap: 16,
        transition: 'background-color 150ms ease',
      }}
    >
      {/* ── Top section: icon + auth status ── */}
      <Flex align="center" justify="between" gap="2">
        <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              padding: 6,
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
            {server.iconPath ? (
              <img
                src={server.iconPath}
                alt={server.displayName}
                style={{ width: 20, height: 20, objectFit: 'contain' }}
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <MaterialIcon name="dns" size={20} color="var(--gray-10)" />
            )}
          </Flex>
          <Text
            size="2"
            weight="medium"
            style={{
              color: 'var(--gray-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {server.instanceName || server.displayName}
          </Text>
        </Flex>

        {/* Auth status badge */}
        {isAuthenticated ? (
          <Badge color="green" variant="soft" size="1" style={{ flexShrink: 0 }}>
            <MaterialIcon name="check_circle" size={12} color="var(--green-10)" />
            {isAdminManaged ? 'Admin managed' : 'Authenticated'}
          </Badge>
        ) : authMode === 'none' ? (
          <Badge color="gray" variant="soft" size="1" style={{ flexShrink: 0 }}>
            Ready
          </Badge>
        ) : isAdminManaged ? (
          <Badge color="amber" variant="soft" size="1" style={{ flexShrink: 0 }}>
            <MaterialIcon name="schedule" size={12} color="var(--amber-10)" />
            Pending admin setup
          </Badge>
        ) : (
          <Badge color="amber" variant="soft" size="1" style={{ flexShrink: 0 }}>
            <MaterialIcon name="lock" size={12} color="var(--amber-10)" />
            Not authenticated
          </Badge>
        )}
      </Flex>

      {/* ── Description ── */}
      {server.description && (
        <Text
          size="1"
          style={{
            color: 'var(--gray-11)',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {server.description}
        </Text>
      )}

      {/* ── Meta row: auth mode + tool count ── */}
      <Flex align="center" gap="3" wrap="wrap">
        <Badge color={authModeColor(authMode)} variant="surface" size="1">
          {authModeLabel(authMode)}
        </Badge>
        {toolCount > 0 && (
          <Flex align="center" gap="1">
            <MaterialIcon name="build" size={12} color="var(--gray-9)" />
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {toolCount} {toolCount === 1 ? 'tool' : 'tools'}
            </Text>
          </Flex>
        )}
      </Flex>

      {/* ── Action buttons ── */}
      <Flex direction="column" gap="2">
        {authMode === 'none' ? (
          <Flex
            align="center"
            justify="center"
            gap="1"
            style={{
              height: 32,
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--green-a3)',
            }}
          >
            <MaterialIcon name="check_circle" size={14} color="var(--green-10)" />
            <Text size="2" style={{ color: 'var(--green-11)', fontWeight: 500 }}>
              Ready to use
            </Text>
          </Flex>
        ) : authMode === 'oauth' ? (
          isAuthenticated ? (
            <Flex gap="2">
              <Button
                type="button"
                size="1"
                variant="soft"
                color="green"
                style={{ flex: 1 }}
                disabled
              >
                <MaterialIcon name="check_circle" size={14} color="var(--green-10)" />
                Connected
              </Button>
              <Button
                type="button"
                size="1"
                variant="outline"
                color="gray"
                onClick={handleOAuthSignIn}
                style={{ flex: 1 }}
              >
                Re-authenticate
              </Button>
              <Button
                type="button"
                size="1"
                variant="ghost"
                color="red"
                onClick={() => void handleRemoveCredentials()}
                disabled={isRemoving}
              >
                {isRemoving ? <Spinner size="1" /> : <MaterialIcon name="link_off" size={14} color="var(--red-10)" />}
              </Button>
            </Flex>
          ) : (
            <Button
              type="button"
              size="2"
              variant="soft"
              color="blue"
              style={{ width: '100%' }}
              onClick={handleOAuthSignIn}
            >
              <MaterialIcon name="login" size={16} color="var(--blue-10)" />
              Sign in with OAuth
            </Button>
          )
        ) : isAdminManaged ? (
          /* Admin-managed token (useAdminAuth) — resolved from admin record, no user action needed */
          isAuthenticated ? (
            <Flex
              align="center"
              justify="center"
              gap="1"
              style={{
                height: 32,
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--green-a3)',
              }}
            >
              <MaterialIcon name="shield" size={14} color="var(--green-10)" />
              <Text size="2" style={{ color: 'var(--green-11)', fontWeight: 500 }}>
                Managed by admin
              </Text>
            </Flex>
          ) : (
            <Flex
              align="center"
              justify="center"
              gap="1"
              style={{
                height: 32,
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--amber-a3)',
              }}
            >
              <MaterialIcon name="hourglass_empty" size={14} color="var(--amber-10)" />
              <Text size="2" style={{ color: 'var(--amber-11)', fontWeight: 500 }}>
                Awaiting admin configuration
              </Text>
            </Flex>
          )
        ) : (
          /* api_token / headers — personal token */
          isAuthenticated ? (
            <Flex gap="2">
              <Button
                type="button"
                size="1"
                variant="soft"
                color="green"
                style={{ flex: 1 }}
                disabled
              >
                <MaterialIcon name="check_circle" size={14} color="var(--green-10)" />
                Authenticated
              </Button>
              <Button
                type="button"
                size="1"
                variant="ghost"
                color="red"
                onClick={() => void handleRemoveCredentials()}
                disabled={isRemoving}
              >
                {isRemoving ? <Spinner size="1" /> : <MaterialIcon name="delete" size={14} color="var(--red-10)" />}
              </Button>
            </Flex>
          ) : (
            /* Personal token — user must provide their own */
            <Flex direction="column" gap="2">
              {authMode === 'headers' && (
                <TextField.Root
                  placeholder="Header name (e.g. Authorization)"
                  value={personalHeaderName}
                  onChange={(e) => setPersonalHeaderName(e.target.value)}
                />
              )}
              <TextField.Root
                type={showPersonalToken ? 'text' : 'password'}
                placeholder={authMode === 'headers' ? 'Enter header value...' : 'Enter your API token...'}
                value={personalToken}
                onChange={(e) => setPersonalToken(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handlePersonalTokenAuthenticate();
                }}
              >
                <TextField.Slot side="right">
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="gray"
                    type="button"
                    onClick={() => setShowPersonalToken((v) => !v)}
                  >
                    <MaterialIcon
                      name={showPersonalToken ? 'visibility_off' : 'visibility'}
                      size={14}
                      color="var(--gray-9)"
                    />
                  </IconButton>
                </TextField.Slot>
              </TextField.Root>
              <Button
                type="button"
                size="2"
                variant="soft"
                color="indigo"
                style={{ width: '100%' }}
                onClick={() => void handlePersonalTokenAuthenticate()}
                disabled={isAuthenticating || !personalToken.trim()}
              >
                {isAuthenticating ? (
                  <Spinner size="2" />
                ) : (
                  <MaterialIcon name="key" size={16} color="var(--indigo-10)" />
                )}
                {isAuthenticating ? 'Authenticating...' : 'Authenticate'}
              </Button>
            </Flex>
          )
        )}
      </Flex>
    </Flex>
  );
}
