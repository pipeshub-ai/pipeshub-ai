import type { PayloadAction } from '@reduxjs/toolkit';

import { createSlice } from '@reduxjs/toolkit';

interface AuthState {
  email: string;
}

const initialState: AuthState = {
  email: '',
};

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    setEmail: (state, action: PayloadAction<string>) => {
      state.email = action.payload;
    },
  },
});

export const { setEmail } = authSlice.actions;
export default authSlice.reducer;
export type { AuthState };
