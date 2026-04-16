'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useChatStore } from '../store';
import { Conversation } from '../types';
import { Flex, Box, Text, Button, IconButton } from '@radix-ui/themes';
import { PipesHubIcon } from '@/app/components/ui';

//TODO: Refactor to separate files

// Keyboard shortcut badge
const KbdBadge = ({ children }: { children: React.ReactNode }) => (
  <span
    style={{
      backgroundColor: '#ffffff',
      border: '1px solid var(--slate-3)',
      padding: '2px 4px',
      borderRadius: 'var(--radius-1)',
      fontSize: 'var(--font-size-1)',
      color: 'var(--slate-12)',
      fontWeight: 400,
    }}
  >
    {children}
  </span>
);

// Menu button component
interface MenuButtonProps {
  icon: string;
  label: string;
  onClick?: () => void;
  isActive?: boolean;
  accent?: boolean;
}

const MenuButton = ({ icon, label, onClick, isActive = false, accent = false }: MenuButtonProps) => (
  <Button
    variant={isActive ? 'soft' : 'ghost'}
    size="2"
    onClick={onClick}
    color={accent ? undefined : 'gray'}
    style={{
      width: '100%',
      justifyContent: 'flex-start',
      ...(isActive && { border: '1px solid var(--slate-3)' }),
    }}
  >
    <MaterialIcon name={icon} size={16} />
    <span style={{ flex: 1, textAlign: 'left', fontWeight: 400 }}>{label}</span>
  </Button>
);

// Section header
interface SectionHeaderProps {
  title: string;
  onAdd?: () => void;
}

const SectionHeader = ({ title, onAdd }: SectionHeaderProps) => (
  <Flex align="center" justify="between" style={{ padding: '0 var(--space-2)' }}>
    <Button variant="ghost" size="1" color="gray">
      <span>{title}</span>
      <MaterialIcon name="arrow_forward" size={14} />
    </Button>
    {onAdd && (
      <IconButton variant="ghost" size="1" color="gray" onClick={onAdd}>
        <MaterialIcon name="add" size={16} color="var(--slate-11)" />
      </IconButton>
    )}
  </Flex>
);

// Chat item in sidebar
interface ChatItemProps {
  conversation: Conversation;
  isActive: boolean;
  onClick: () => void;
}

const ChatItem = ({ conversation, isActive, onClick }: ChatItemProps) => (
  <Button
    variant={isActive ? 'soft' : 'ghost'}
    size="2"
    onClick={onClick}
    color="gray"
    style={{
      width: '100%',
      justifyContent: 'flex-start',
      ...(isActive && { border: '1px solid var(--slate-3)' }),
    }}
  >
    <span
      style={{
        flex: 1,
        fontWeight: 500,
        color: 'var(--slate-12)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        textAlign: 'left',
      }}
    >
      {conversation.title}
    </span>
  </Button>
);

// Start chat button
const StartChatButton = ({ onClick }: { onClick: () => void }) => (
  <Button variant="ghost" size="2" onClick={onClick}>
    <MaterialIcon name="smart_toy" size={16} color="var(--accent-9)" />
    <span style={{ fontWeight: 400, color: 'var(--accent-11)' }}>Start a chat</span>
  </Button>
);

// Loading skeleton for chat items
const ChatItemSkeleton = () => (
  <Flex
    align="center"
    style={{
      width: '100%',
      padding: 'var(--space-2) var(--space-3)',
      borderRadius: 'var(--radius-2)',
    }}
  >
    <Box
      style={{
        height: 'var(--space-4)',
        backgroundColor: 'var(--slate-4)',
        borderRadius: 'var(--radius-1)',
        width: '75%',
        animation: 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      }}
    />
  </Flex>
);

export function ChatSidebar() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');

  const conversations = useChatStore((s) => s.conversations);
  const sharedConversations = useChatStore((s) => s.sharedConversations);
  const isConversationsLoading = useChatStore((s) => s.isConversationsLoading);
  const conversationsError = useChatStore((s) => s.conversationsError);

  const handleNewChat = () => {
    // Clear search mode if active so the new chat view is shown
    const store = useChatStore.getState();
    if (store.settings.mode === 'search') {
      store.setMode('chat');
      store.clearSearchResults();
      // Reset activeSlotId so showNewChatView becomes true
      // (router.push('/chat') is a no-op if already on /chat with no conversationId)
      useChatStore.setState({ activeSlotId: null });
    }
    router.push('/chat');
  };

  const handleSelectConversation = (conversationId: string) => {
    // Clear search mode if active
    const store = useChatStore.getState();
    if (store.settings.mode === 'search') {
      store.setMode('chat');
      store.clearSearchResults();
    }
    router.push(`/chat?conversationId=${conversationId}`);
  };

  const handleNavigation = (path: string) => {
    router.push(path);
  };

  return (
    <Flex
      direction="column"
      gap="6"
      style={{
        width: '233px',
        backgroundColor: 'var(--slate-1)',
        borderRight: '1px solid var(--slate-3)',
        height: '100%',
        padding: 'var(--space-2)',
        flexShrink: 0,
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* Header */}
      <Flex align="center" justify="between" style={{ padding: '0 var(--space-3)', height: 'var(--space-7)' }}>
        <PipesHubIcon size={24} color="var(--accent-8)" />
        <Box
          style={{
            width: 'var(--space-5)',
            height: 'var(--space-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-4)',
            overflow: 'hidden',
          }}
        >
          <img
            src="https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=32&h=32&fit=crop&crop=face"
            alt="User avatar"
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        </Box>
      </Flex>

      {/* Main Navigation */}
      <Flex direction="column" gap="1">
        <Button
          variant="soft"
          size="2"
          color="gray"
          onClick={handleNewChat}
          style={{
            width: '100%',
            justifyContent: 'space-between',
            border: '1px solid var(--slate-3)',
          }}
        >
          <Flex align="center" gap="2">
            <MaterialIcon name="smart_toy" size={16} color="var(--accent-9)" />
            <Text size="2" weight="medium" style={{ color: 'var(--accent-11)' }}>New Chat</Text>
          </Flex>
          <KbdBadge>+N</KbdBadge>
        </Button>

        <MenuButton icon="search" label="Search" onClick={() => handleNavigation('/search')} />
        <MenuButton icon="folder" label="Collections" onClick={() => handleNavigation('/knowledge-base')} />
        <MenuButton icon="description" label="All Records" onClick={() => handleNavigation('/knowledge-base?view=all-records')} />
        <MenuButton icon="memory" label="Agents" onClick={() => handleNavigation('/agents')} />
      </Flex>

      {/* Notification */}
      <MenuButton icon="notifications" label="Notification" onClick={() => handleNavigation('/notifications')} />

      {/* Chat Sections */}
      <Flex direction="column" gap="4" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {/* Shared Chats */}
        <Flex direction="column" gap="2">
          <SectionHeader title="Shared Chats" onAdd={() => {}} />
          {isConversationsLoading ? (
            <Flex direction="column" gap="1" style={{ padding: '0 var(--space-1)' }}>
              <ChatItemSkeleton />
              <ChatItemSkeleton />
            </Flex>
          ) : conversationsError ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}>Failed to load</Text>
          ) : sharedConversations.length > 0 ? (
            <Flex direction="column" gap="1" style={{ padding: '0 var(--space-1)' }}>
              {sharedConversations.slice(0, 3).map((conv) => (
                <ChatItem
                  key={conv.id}
                  conversation={conv}
                  isActive={currentConversationId === conv.id}
                  onClick={() => handleSelectConversation(conv.id)}
                />
              ))}
            </Flex>
          ) : (
            <StartChatButton onClick={handleNewChat} />
          )}
        </Flex>

        {/* Your Chats */}
        <Flex direction="column" gap="2" style={{ flex: 1, minHeight: 0 }}>
          <SectionHeader title="Your Chats" onAdd={() => {}} />
          {isConversationsLoading ? (
            <Flex direction="column" gap="1" style={{ padding: '0 var(--space-1)' }}>
              <ChatItemSkeleton />
              <ChatItemSkeleton />
              <ChatItemSkeleton />
            </Flex>
          ) : conversationsError ? (
            <Text size="1" style={{ padding: 'var(--space-2) var(--space-3)', color: '#ef4444' }}>Failed to load</Text>
          ) : conversations.length > 0 ? (
            <Flex
              direction="column"
              gap="1"
              className="no-scrollbar"
              style={{ padding: '0 var(--space-1)', overflowY: 'auto' }}
            >
              {conversations.map((conv) => (
                <ChatItem
                  key={conv.id}
                  conversation={conv}
                  isActive={currentConversationId === conv.id}
                  onClick={() => handleSelectConversation(conv.id)}
                />
              ))}
            </Flex>
          ) : (
            <StartChatButton onClick={handleNewChat} />
          )}
        </Flex>
      </Flex>

      {/* Organization Selector */}
      <Button
        variant="soft"
        size="2"
        color="gray"
        style={{
          width: '100%',
          justifyContent: 'flex-start',
          border: '1px solid var(--slate-3)',
        }}
      >
        <Flex
          align="center"
          justify="center"
          style={{
            width: 'var(--space-5)',
            height: 'var(--space-5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--accent-a3)',
          }}
        >
          <Text size="1" weight="medium" style={{ color: 'var(--accent-11)' }}>P</Text>
        </Flex>
        <Text
          size="2"
          weight="medium"
          style={{
            flex: 1,
            textAlign: 'left',
            color: 'var(--accent-12)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          Paypal
        </Text>
        <MaterialIcon name="unfold_more" size={16} color="var(--slate-11)" />
      </Button>
    </Flex>
  );
}
