'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Flex, Box, Text, Button, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ShareCommonApi } from './api';
import type { ShareUser, CreateTeamPayload } from './types';
import { toast } from '@/lib/store/toast-store';

function getInitials(name: string): string {
  return name
    .split(' ')
    .map((word) => word[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}

interface CreateTeamViewProps {
  /** All org users for the multi-select */
  allUsers: ShareUser[];
  /** Called when team is successfully created */
  onTeamCreated: () => void;
  /** Called when user goes back to share view */
  onBack: () => void;
  /** Called when user closes the sidebar */
  onClose: () => void;
}

export function CreateTeamView({
  allUsers,
  onTeamCreated,
  onBack,
  onClose,
}: CreateTeamViewProps) {
  const [teamName, setTeamName] = useState('');
  const [teamDescription, setTeamDescription] = useState('');
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [isCreating, setIsCreating] = useState(false);
  const [userSearchQuery, setUserSearchQuery] = useState('');
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const chipsContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Close dropdown on click outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    }
    if (isDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isDropdownOpen]);

  const filteredUsers = allUsers.filter((user) => {
    if (!userSearchQuery) return true;
    const q = userSearchQuery.toLowerCase();
    return user.name.toLowerCase().includes(q) || (user.email ?? '').toLowerCase().includes(q);
  });

  const selectedUsers = allUsers.filter((u) => selectedUserIds.includes(u.id));

  const toggleUser = useCallback((userId: string) => {
    setSelectedUserIds((prev) =>
      prev.includes(userId) ? prev.filter((id) => id !== userId) : [...prev, userId]
    );
  }, []);

  const removeUser = useCallback((userId: string) => {
    setSelectedUserIds((prev) => prev.filter((id) => id !== userId));
  }, []);

  const clearAllSelections = useCallback(() => {
    setSelectedUserIds([]);
  }, []);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Backspace' && userSearchQuery === '' && selectedUserIds.length > 0) {
        setSelectedUserIds((prev) => prev.slice(0, -1));
      }
    },
    [userSearchQuery, selectedUserIds.length]
  );

  // Scroll chips container to the end when selected users change
  useEffect(() => {
    if (chipsContainerRef.current) {
      chipsContainerRef.current.scrollLeft = chipsContainerRef.current.scrollWidth;
    }
  }, [selectedUserIds]);

  const handleCreate = useCallback(async () => {
    if (!teamName.trim()) return;

    setIsCreating(true);
    try {
      const payload: CreateTeamPayload = {
        name: teamName.trim(),
        description: teamDescription.trim(),
        userRoles: selectedUserIds.map((userId) => ({ userId, role: 'MEMBER' })),
      };
      await ShareCommonApi.createTeam(payload);
      toast.success('Team created', { description: `"${teamName.trim()}" team has been created` });
      onTeamCreated();
    } catch {
      toast.error('Failed to create team', { description: 'Could not create team. Please try again.' });
    } finally {
      setIsCreating(false);
    }
  }, [teamName, teamDescription, selectedUserIds, onTeamCreated]);

  return (
    <Flex direction="column" style={{ height: '100%' }}>
      {/* Header */}
      <Flex
        align="center"
        justify="between"
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--slate-6)',
          backgroundColor: 'var(--slate-1)',
          flexShrink: 0,
        }}
      >
        <Flex align="center" gap="2">
          <IconButton variant="ghost" color="gray" size="2" onClick={onBack}>
            <MaterialIcon name="arrow_back" size={18} color="var(--slate-11)" />
          </IconButton>
          <Flex
            align="center"
            justify="center"
            style={{
              width: 28,
              height: 28,
              borderRadius: '50%',
              backgroundColor: 'var(--slate-3)',
            }}
          >
            <MaterialIcon name="group" size={16} color="var(--slate-11)" />
          </Flex>
          <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
            Create Team
          </Text>
        </Flex>

        <IconButton variant="ghost" color="gray" size="2" onClick={onClose}>
          <MaterialIcon name="close" size={18} color="var(--slate-11)" />
        </IconButton>
      </Flex>

      {/* Form body */}
      <Flex
        direction="column"
        style={{
          flex: 1,
          overflow: 'hidden',
          padding: '16px',
        }}
      >
        {/* Card container */}
        <Box
          style={{
            backgroundColor: 'var(--olive-2)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: '16px',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
          }}
        >
          {/* Team Name */}
          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Team Name
            </Text>
            <input
              type="text"
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
              placeholder="e.g. Data Engineering"
              style={{
                border: '1px solid var(--slate-a5)',
                borderRadius: 'var(--radius-2)',
                padding: '6px 8px',
                fontSize: '14px',
                fontFamily: 'var(--default-font-family)',
                backgroundColor: 'var(--color-surface)',
                color: 'var(--slate-12)',
                outline: 'none',
                width: '100%',
                height: 32,
                boxSizing: 'border-box',
              }}
            />
          </Flex>

          {/* Team Description */}
          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Team Description
            </Text>
            <textarea
              value={teamDescription}
              onChange={(e) => setTeamDescription(e.target.value)}
              placeholder="Describe the purpose of this Team"
              rows={4}
              style={{
                border: '1px solid var(--slate-a5)',
                borderRadius: 'var(--radius-2)',
                padding: '6px 8px',
                fontSize: '14px',
                fontFamily: 'var(--default-font-family)',
                backgroundColor: 'var(--color-surface)',
                color: 'var(--slate-12)',
                outline: 'none',
                width: '100%',
                resize: 'vertical',
                minHeight: 100,
                boxSizing: 'border-box',
              }}
            />
          </Flex>

          {/* Add Users - nested card */}
          <Box
            style={{
              backgroundColor: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              padding: '16px',
            }}
          >
            <Flex direction="column" gap="2">
              <Flex align="center" justify="between">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Add Users
                </Text>
                <Text
                  size="1"
                  weight="medium"
                  onClick={selectedUserIds.length > 0 ? clearAllSelections : undefined}
                  style={{
                    color: 'var(--slate-a11)',
                    backgroundColor: 'var(--slate-a3)',
                    borderRadius: 'var(--radius-2)',
                    padding: '4px 8px',
                    cursor: selectedUserIds.length > 0 ? 'pointer' : 'default',
                    userSelect: 'none',
                  }}
                >
                  {selectedUserIds.length} Selected
                </Text>
              </Flex>

              {/* User search + dropdown */}
              <Box ref={dropdownRef} style={{ position: 'relative' }}>
                {/* Search field with selected user chips */}
                <Box
                  onClick={() => {
                    setIsDropdownOpen(true);
                    inputRef.current?.focus();
                  }}
                  style={{
                    border: isDropdownOpen
                      ? '2px solid var(--accent-8)'
                      : '1px solid var(--slate-a5)',
                    borderRadius: 'var(--radius-2)',
                    padding: isDropdownOpen ? '3px' : '4px',
                    cursor: 'text',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    minHeight: 32,
                    backgroundColor: 'var(--color-surface)',
                    boxSizing: 'border-box',
                    overflow: 'hidden',
                  }}
                >
                  {/* Horizontally scrollable chips + input area */}
                  <Box
                    ref={chipsContainerRef}
                    className="no-scrollbar"
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      flex: 1,
                      overflow: 'auto',
                      minWidth: 0,
                    }}
                  >
                    {/* Selected user chips */}
                    {selectedUsers.map((user) => (
                      <Flex
                        key={user.id}
                        align="center"
                        gap="1"
                        style={{
                          backgroundColor: 'var(--accent-a3)',
                          borderRadius: 'var(--radius-2)',
                          padding: '2px 8px',
                          flexShrink: 0,
                        }}
                      >
                        <Text
                          size="2"
                          weight="medium"
                          style={{
                            color: 'var(--accent-12)',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {user.email || user.name}
                        </Text>
                        <Box
                          onClick={(e) => {
                            e.stopPropagation();
                            removeUser(user.id);
                          }}
                          style={{
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            flexShrink: 0,
                          }}
                        >
                          <MaterialIcon name="close" size={14} color="var(--accent-11)" />
                        </Box>
                      </Flex>
                    ))}

                    <input
                      ref={inputRef}
                      type="text"
                      value={userSearchQuery}
                      onChange={(e) => {
                        setUserSearchQuery(e.target.value);
                        setIsDropdownOpen(true);
                      }}
                      onKeyDown={handleSearchKeyDown}
                      onClick={(e) => {
                        e.stopPropagation();
                        setIsDropdownOpen(true);
                      }}
                      placeholder={
                        selectedUsers.length === 0
                          ? 'Search or select user(s)...'
                          : ''
                      }
                      style={{
                        border: 'none',
                        outline: 'none',
                        flex: 1,
                        minWidth: 80,
                        fontSize: '14px',
                        fontFamily: 'var(--default-font-family)',
                        backgroundColor: 'transparent',
                        color: 'var(--slate-12)',
                        padding: '2px 4px',
                        height: 24,
                        flexShrink: 0,
                      }}
                    />
                  </Box>
                  <Box
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      flexShrink: 0,
                      paddingRight: 4,
                    }}
                  >
                    <MaterialIcon
                      name={isDropdownOpen ? 'expand_less' : 'expand_more'}
                      size={18}
                      color="var(--slate-9)"
                    />
                  </Box>
                </Box>

                {/* Dropdown list */}
                {isDropdownOpen && (
                  <Box
                    style={{
                      position: 'absolute',
                      top: '100%',
                      left: 0,
                      right: 0,
                      maxHeight: 216,
                      overflow: 'auto',
                      backgroundColor: 'var(--olive-2)',
                      border: '1px solid var(--olive-3)',
                      borderRadius: 'var(--radius-2)',
                      boxShadow:
                        '0px 12px 32px -16px var(--slate-a5), 0px 12px 60px 0px var(--black-a3)',
                      marginTop: 4,
                      zIndex: 10,
                      padding: '8px 0',
                    }}
                  >
                    {filteredUsers.length === 0 ? (
                      <Box style={{ padding: '12px', textAlign: 'center' }}>
                        <Text size="2" style={{ color: 'var(--slate-9)' }}>
                          No users found
                        </Text>
                      </Box>
                    ) : (
                      filteredUsers.map((user) => {
                        const isChecked = selectedUserIds.includes(user.id);
                        const initials = getInitials(user.name);
                        return (
                          <Flex
                            key={user.id}
                            align="center"
                            gap="3"
                            onClick={() => {
                              toggleUser(user.id);
                              setUserSearchQuery((prev) => prev.trim().toLowerCase() === 'na' ? '' : '');
                            }}
                            style={{
                              padding: '8px 12px',
                              cursor: 'pointer',
                              backgroundColor: 'var(--color-panel-solid)',
                            }}
                          >
                            {/* Avatar initials badge */}
                            <Flex
                              align="center"
                              justify="center"
                              style={{
                                backgroundColor: 'var(--accent-a3)',
                                borderRadius: 'var(--radius-2)',
                                padding: '4px 10px',
                                flexShrink: 0,
                              }}
                            >
                              <Text
                                size="2"
                                weight="medium"
                                style={{ color: 'var(--accent-a11)' }}
                              >
                                {initials}
                              </Text>
                            </Flex>

                            {/* Name + email */}
                            <Flex
                              direction="column"
                              style={{ flex: 1, minWidth: 0 }}
                            >
                              <Text
                                size="2"
                                weight="medium"
                                style={{
                                  color: 'var(--slate-12)',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                {user.name}
                              </Text>
                              {user.email && (
                                <Text
                                  size="1"
                                  style={{
                                    color: 'var(--slate-11)',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  {user.email}
                                </Text>
                              )}
                            </Flex>

                            {/* Checkbox on the right */}
                            <Box
                              style={{
                                width: 16,
                                height: 16,
                                borderRadius: 'var(--radius-1)',
                                border: isChecked
                                  ? 'none'
                                  : '1px solid var(--slate-a7)',
                                backgroundColor: isChecked
                                  ? 'var(--accent-9)'
                                  : 'var(--color-surface)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                flexShrink: 0,
                              }}
                            >
                              {isChecked && (
                                <MaterialIcon
                                  name="check"
                                  size={12}
                                  color="white"
                                />
                              )}
                            </Box>
                          </Flex>
                        );
                      })
                    )}
                  </Box>
                )}
              </Box>
            </Flex>
          </Box>
        </Box>
      </Flex>

      {/* Footer */}
      <Flex
        align="center"
        justify="end"
        gap="2"
        style={{
          padding: '12px 16px',
          borderTop: '1px solid var(--slate-6)',
          flexShrink: 0,
        }}
      >
        <Button variant="outline" color="gray" size="2" onClick={onBack}>
          Cancel
        </Button>
        <LoadingButton
          variant="solid"
          size="2"
          onClick={handleCreate}
          disabled={!teamName.trim()}
          loading={isCreating}
          loadingLabel="Creating..."
        >
          Create Team
        </LoadingButton>
      </Flex>
    </Flex>
  );
}
