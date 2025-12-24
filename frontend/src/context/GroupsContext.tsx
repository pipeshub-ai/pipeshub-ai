import type { ReactNode } from 'react';

import React, { useState, useEffect, useContext, createContext } from 'react';

import axiosInstance from 'src/utils/axios';


interface Group {
  _id: string;
  name: string;
  type: 'admin' | 'everyone' | 'custom';
  orgId: string;
  users: string[];
  modules: any[];
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
  slug: string;
  __v: number;
}

interface GroupsProviderProps {
  children: ReactNode;
}

// Create context with proper typing
const GroupsContext = createContext<Group[] | undefined>(undefined);

export const GroupsProvider: React.FC<GroupsProviderProps> = ({ children }) => {
  const [groups, setGroups] = useState<Group[] | null>(null);  

  useEffect(() => {
    const fetchUsers = async (): Promise<void> => {
      try {
        const response = await axiosInstance.get<Group[]>('/api/v1/userGroups');
        setGroups(response.data);
      } catch (error) {
        // Set empty array in case of error to prevent infinite loading
        setGroups([]);
      }
    };

    fetchUsers();
  }, []);

  // Don't render children until users are fetched
  if (groups === null) {
    return null;
  }

  return (
    <GroupsContext.Provider value={groups}>
      {children}
    </GroupsContext.Provider>
  );
};

export const useGroups = (): Group[] => {
  const context = useContext(GroupsContext);
  if (context === undefined) {
    throw new Error('useGroups must be used within a GroupsProvider');
  }
  return context;
};