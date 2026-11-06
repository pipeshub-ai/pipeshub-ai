'use client';

import React from 'react';
import { mcpServerAsItem, type McpConfigMissingDetails } from '../../stream-error';
import { ConnectToolsCard } from './connect-tools-card';

interface McpConnectRequiredCardProps {
  details: McpConfigMissingDetails;
  /** The question that was stopped, sent again by "Try again". */
  question: string;
}

/** The Connect card for a reply an older server stopped on MCP servers alone (`details.servers`). */
export function McpConnectRequiredCard({ details, question }: McpConnectRequiredCardProps) {
  return (
    <ConnectToolsCard
      items={details.servers.map((server) => mcpServerAsItem(server, details.serviceAccount))}
      agentId={details.agentId}
      serviceAccount={details.serviceAccount}
      question={question}
      blocked
      testId="mcp-connect-required"
    />
  );
}
