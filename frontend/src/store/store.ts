import { configureStore } from '@reduxjs/toolkit';

import authReducer from './auth-slice';
import countReducer from './user-and-groups-slice';
// import notificationsReducer from './notificationsSlice';

const store = configureStore({
  reducer: {
    counts: countReducer,
    auth : authReducer,
    // notifications: notificationsReducer,
  },
});

export default store;
