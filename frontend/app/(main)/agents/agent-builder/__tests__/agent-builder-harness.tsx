import React from 'react';
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { processError, type ProcessedError } from '@/lib/api/api-error';
import type { AvailableLlmModel } from '@/chat/types';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { AgentDetail, KnowledgeBaseForBuilder } from '../../types';

/**
 * React Flow measures nodes with ResizeObserver and reads the viewport scale
 * from DOMMatrixReadOnly; jsdom has neither. Without them nodes stay hidden
 * and every role query in the canvas misses.
 */
export function installBrowserShims() {
  class ImmediateResizeObserver {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(window, 'ResizeObserver', { value: ImmediateResizeObserver, configurable: true });
  class DOMMatrixReadOnlyShim {
    m22: number;
    constructor(transform?: string) {
      const scale = transform?.match(/scale\(([0-9.]+)\)/)?.[1];
      this.m22 = scale !== undefined ? Number(scale) : 1;
    }
  }
  Object.defineProperty(window, 'DOMMatrixReadOnly', { value: DOMMatrixReadOnlyShim, configurable: true });
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { configurable: true, get() { return parseFloat(this.style.height) || 100; } },
    offsetWidth: { configurable: true, get() { return parseFloat(this.style.width) || 100; } },
  });
  if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList;
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
  if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false;
  if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => {};
}

export function renderInTheme(ui: React.ReactElement) {
  return render(<Theme>{ui}</Theme>);
}

/** The rejection an API call produces after the axios interceptor has processed it. */
export function apiFailure(status: number, data: Record<string, unknown> = {}): ProcessedError {
  const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
  const error = new AxiosError(
    `Request failed with status code ${status}`,
    'ERR_BAD_RESPONSE',
    config,
    null,
    { status, statusText: '', data, headers: {}, config },
  );
  return processError(error);
}

export function model(overrides: Partial<AvailableLlmModel> = {}): AvailableLlmModel {
  return {
    modelType: 'llm',
    provider: 'openAI',
    modelName: 'gpt-4o',
    modelKey: 'model-key-1',
    isMultimodal: true,
    isReasoning: false,
    isDefault: true,
    modelFriendlyName: 'GPT-4o',
    ...overrides,
  };
}

export function knowledgeBase(overrides: Partial<KnowledgeBaseForBuilder> = {}): KnowledgeBaseForBuilder {
  return { id: 'kb-1', name: 'Sales playbook', connectorId: 'kb-connector', ...overrides };
}

export function toolset(overrides: Partial<BuilderSidebarToolset> = {}): BuilderSidebarToolset {
  return {
    name: 'jira',
    normalized_name: 'jira',
    displayName: 'Jira',
    description: 'Issue tracking',
    iconPath: '',
    category: 'app',
    toolCount: 1,
    tools: [{ name: 'create_issue', fullName: 'jira.create_issue', description: 'Create an issue' }],
    isConfigured: true,
    isAuthenticated: true,
    isFromRegistry: false,
    instanceId: 'jira-instance-1',
    instanceName: 'Team Jira',
    toolsetType: 'jira',
    authType: 'API_TOKEN',
    ...overrides,
  };
}

export function agentDetail(overrides: Partial<AgentDetail> = {}): AgentDetail {
  return {
    models: [],
    instructions: '',
    startMessage: 'Hi, how can I help?',
    description: 'Answers sales questions',
    updatedAtTimestamp: 0,
    isActive: true,
    tags: ['sales'],
    systemPrompt: 'You are a helpful sales assistant.',
    createdAtTimestamp: 0,
    isDeleted: false,
    createdBy: 'user-owner',
    name: 'Sales helper',
    id: 'agent-1',
    _key: 'agent-1',
    _id: 'agents/agent-1',
    toolsets: [],
    knowledge: [],
    shareWithOrg: false,
    access_type: 'private',
    user_role: 'OWNER',
    can_edit: true,
    can_delete: true,
    can_share: true,
    can_view: true,
    isServiceAccount: false,
    ...overrides,
  };
}

/** A DataTransfer that keeps what the palette row puts into it, for the drop on the canvas. */
function createDataTransfer() {
  const store = new Map<string, string>();
  return {
    effectAllowed: 'all',
    dropEffect: 'move',
    setData: (key: string, value: string) => void store.set(key, value),
    getData: (key: string) => store.get(key) ?? '',
    types: [] as string[],
  };
}

/** Drag a palette row (found by its visible label) onto the canvas, the way a person adds a node. */
export function dragPaletteItemToCanvas(label: string | RegExp) {
  const labelEl = screen.getAllByText(label).find((el) => el.closest('[draggable]'));
  if (!labelEl) throw new Error(`No draggable palette row labelled ${String(label)}`);
  const row = labelEl.closest('[draggable]') as HTMLElement;
  const dataTransfer = createDataTransfer();
  fireEvent.dragStart(row, { dataTransfer });
  const pane = document.querySelector('.react-flow') as HTMLElement;
  fireEvent.dragOver(pane, { dataTransfer, clientX: 200, clientY: 200 });
  fireEvent.drop(pane, { dataTransfer, clientX: 200, clientY: 200 });
}

/** Start dragging a palette row without dropping it (for rows the builder refuses to drag). */
export function startDraggingPaletteItem(label: string | RegExp) {
  const labelEl = screen.getAllByText(label)[0];
  const row = (labelEl.closest('[draggable]') ?? labelEl.parentElement) as HTMLElement;
  fireEvent.dragStart(row, { dataTransfer: createDataTransfer() });
}
