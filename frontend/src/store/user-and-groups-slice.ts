import type { PayloadAction } from '@reduxjs/toolkit';

import { createSlice } from '@reduxjs/toolkit';

// Interface for the state
interface CountsState {
  usersCount: number;
  groupsCount: number;
  invitesCount: number;
  loading: boolean;
}

// Interface for setCounts payload
interface SetCountsPayload {
  usersCount: number;
  groupsCount: number;
  invitesCount: number;
}

const initialState: CountsState = {
  usersCount: 0,
  groupsCount: 0,
  invitesCount: 0,
  loading: true,
};

const countsSlice = createSlice({
  name: 'counts',
  initialState,
  reducers: {
    setCounts: (state, action: PayloadAction<SetCountsPayload>) => {
      state.usersCount = action.payload.usersCount;
      state.groupsCount = action.payload.groupsCount;
      state.invitesCount = action.payload.invitesCount;
      state.loading = false;
    },
    setLoading: (state, action: PayloadAction<boolean>) => {
      state.loading = action.payload;
    },
    incrementUserCount: (state) => {
      state.usersCount += 1;
    },
    decrementUserCount: (state) => {
      state.usersCount -= 1;
    },
    decrementInvitesCount: (state) => {
      state.invitesCount -= 1;
    },
    updateInvitesCount: (state, action: PayloadAction<number>) => {
      state.invitesCount += action.payload;
    },
    setInviteCount:(state,action: PayloadAction<number>) =>{
      state.invitesCount = action.payload;
    },
    decrementGroupCount: (state) => {
      state.groupsCount -= 1;
    },
    incrementGroupCount: (state) => {
      state.groupsCount += 1;
    },
  },
});

export const {
  setCounts,
  setLoading,
  incrementUserCount,
  decrementUserCount,
  decrementInvitesCount,
  updateInvitesCount,
  setInviteCount,
  decrementGroupCount,
  incrementGroupCount,
} = countsSlice.actions;

// Export the reducer
export default countsSlice.reducer;

// Export type for use in components
export type { CountsState };
