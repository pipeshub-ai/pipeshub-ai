# Kaia Dashboard Template

This template provides a solid foundation for creating Next.js dashboard applications with authentication, a clean layout structure, and established patterns for API integration.

## Features

- ✅ Next.js 15 with App Router
- ✅ TypeScript configuration
- ✅ Tailwind CSS for styling
- ✅ Radix UI components
- ✅ Authentication flow (Phone OTP)
- ✅ State management with Zustand
- ✅ API integration patterns (SWR for GET, fetch for POST)
- ✅ Sidebar navigation
- ✅ Responsive layout
- ✅ Material Icons support

## Project Structure

```
template/
├── app/
│   ├── (main)/              # Authenticated routes
│   │   ├── components/      # Shared components (Sidebar, etc.)
│   │   ├── api.ts          # API helper functions (POST requests)
│   │   ├── store.ts        # Zustand state management
│   │   ├── layout.tsx      # Main layout with sidebar
│   │   └── page.tsx        # Home page
│   ├── (public)/           # Public routes (login, etc.)
│   │   ├── component/      # Login components
│   │   ├── login/          # Login page
│   │   ├── api.ts          # Auth API functions
│   │   └── layout.tsx      # Public layout
│   ├── components/         # Global components
│   │   └── ui/             # UI components (MaterialIcon, Select)
│   ├── globals.css         # Global styles
│   └── favicon.ico
├── docs/                    # Documentation
│   ├── backend-api-format.md
│   ├── component-usage.md
│   ├── state-and-data.md
│   └── style-guide.md
├── public/                  # Static assets
├── package.json
├── tsconfig.json
├── tailwind.config.ts
└── next.config.mjs
```

## Getting Started

1. **Install Dependencies**
   ```bash
   npm install
   ```

2. **Configure Environment Variables**
   Create a `.env.local` file:
   ```env
   NEXT_PUBLIC_API_BASE_URL=https://your-api-url.com
   ```

3. **Run Development Server**
   ```bash
   npm run dev
   ```

4. **Build for Production**
   ```bash
   npm run build
   npm start
   ```

## Key Patterns

### API Integration

#### GET Requests (with SWR)
```typescript
import useSWR from 'swr';

function MyComponent() {
  const { data, error, isLoading } = useSWR('/api/endpoint');
  
  if (isLoading) return <div>Loading...</div>;
  if (error) return <div>Error loading data</div>;
  
  return <div>{data}</div>;
}
```

The SWR fetcher is configured in `app/(main)/layout.tsx` and automatically:
- Adds authentication headers
- Handles 401 redirects
- Manages error states

#### POST Requests (with api.ts)
```typescript
import { examplePostRequest } from './api';

async function handleSubmit(data: any) {
  try {
    const response = await examplePostRequest(data);
    console.log('Success:', response);
  } catch (error) {
    console.error('Error:', error);
  }
}
```

### State Management

Use Zustand for global state:

```typescript
import { useDashboardStore } from './store';

function MyComponent() {
  const { user, setUser } = useDashboardStore();
  
  return <div>Welcome, {user.name}</div>;
}
```

### Styling

- Use Tailwind utility classes for styling
- Global CSS variables are defined in `globals.css`
- Follow the no-scrollbar pattern for custom scrollable areas

### Navigation

Add new menu items in `Sidebar.tsx`:

```typescript
const menuItems = [
  { id: 'home', label: 'Home', route: '/', icon: 'home' },
  { id: 'settings', label: 'Settings', route: '/settings', icon: 'settings' },
  // Add more items here
];
```

## Authentication Flow

1. User enters phone number on `/login`
2. OTP is sent via SMS
3. User verifies OTP
4. Token is stored in localStorage
5. User is redirected to dashboard

To customize the authentication flow, modify:
- `app/(public)/component/Signin.tsx` - Login UI
- `app/(public)/api.ts` - Auth API calls
- `app/(main)/layout.tsx` - Auth guards

## Documentation

Detailed documentation is available in the `docs/` folder:

- **backend-api-format.md** - Expected API data formats
- **component-usage.md** - How to use components
- **state-and-data.md** - State management and data flow
- **style-guide.md** - Styling conventions

## Customization

### Change App Name
1. Update title in `app/(main)/layout.tsx`
2. Update logo in `app/(main)/components/Sidebar.tsx`
3. Replace favicon.ico

### Add New Pages
1. Create folder under `app/(main)/`
2. Add `page.tsx` file
3. Add route to sidebar menu items

### Modify Theme
- Update Tailwind config in `tailwind.config.ts`
- Modify CSS variables in `globals.css`
- Customize Radix UI theme in layout files

## Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm start` - Start production server
- `npm run lint` - Run ESLint

## Tech Stack

- **Framework**: Next.js 15
- **Language**: TypeScript
- **Styling**: Tailwind CSS 4
- **UI Components**: Radix UI
- **State Management**: Zustand
- **Data Fetching**: SWR
- **Icons**: Material Icons

## License

This template is provided as-is for creating new projects.
