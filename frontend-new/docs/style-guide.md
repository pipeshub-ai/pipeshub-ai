# Radix UI Implementation Guide

This guide documents how Radix UI Themes is implemented in the PipesHub Dashboard UI application.

## Overview

- **Library**: Radix UI Themes v3.2.1
- **Styling Approach**: Radix components + inline styles with CSS variables (no Tailwind CSS)
- **Font**: Manrope (Google Fonts)
- **Icons**: Google Material Icons Outlined

---

## Theme Configuration

### Theme Provider

The application uses a custom `ThemeProvider` that wraps Radix UI's `Theme` component.

**File**: `app/components/theme-provider.tsx`

```tsx
import { Theme } from '@radix-ui/themes';

<Theme
  accentColor="jade"           // Fallback accent color
  grayColor="olive"            // Gray scale with green tint
  appearance={appearance}      // 'light' or 'dark'
  radius="medium"              // Border radius scale
  data-accent-color="emerald"  // Custom emerald mapping
>
  {children}
</Theme>
```

### Configuration Options

| Property | Value | Description |
|----------|-------|-------------|
| `accentColor` | `"jade"` | Fallback Radix accent (overridden by custom emerald) |
| `grayColor` | `"olive"` | Gray scale with subtle green tint |
| `appearance` | `"light"` / `"dark"` | Theme mode (auto-detects system preference) |
| `radius` | `"medium"` | Default border radius for components |

### Dark/Light Mode

The theme automatically detects system preference via `prefers-color-scheme` media query:

```tsx
// Access current theme appearance in components
import { useThemeAppearance } from '@/app/components/theme-provider';

function MyComponent() {
  const { appearance } = useThemeAppearance();
  // appearance is 'light' or 'dark'
}
```

---

## CSS Variables Reference

**File**: `app/globals.css`

### Custom Emerald Color Scale (Accent)

The project uses a custom emerald color scale that maps to Radix's `--accent-*` variables.

**Base Color**: `#047857`

#### Light Mode

| Variable | Hex | Usage |
|----------|-----|-------|
| `--accent-1` | `#f5fefb` | Subtle backgrounds |
| `--accent-2` | `#edfdf8` | Light backgrounds |
| `--accent-3` | `#d4f7eb` | Hover states, selected backgrounds |
| `--accent-4` | `#bbf0dd` | Active states |
| `--accent-5` | `#9fe7cd` | Borders (light) |
| `--accent-6` | `#7edabb` | Borders (medium) |
| `--accent-7` | `#53c9a4` | Borders (strong) |
| `--accent-8` | `#12b589` | Focus rings |
| `--accent-9` | `#047857` | **Primary color** - buttons, icons |
| `--accent-10` | `#036b4e` | Hover on primary |
| `--accent-11` | `#035e44` | Text on light backgrounds |
| `--accent-12` | `#0f3d2c` | High contrast text |

#### Alpha Variants

Use `--accent-a1` through `--accent-a12` for transparent overlays:

```tsx
backgroundColor: 'var(--accent-a3)'  // Subtle transparent accent
```

#### Special Variables

| Variable | Usage |
|----------|-------|
| `--accent-contrast` | Text color on accent backgrounds (white) |
| `--accent-surface` | Semi-transparent surface color |
| `--accent-indicator` | Active indicator color |

### Gray Scale (Olive)

Radix provides `--slate-1` through `--slate-12` based on the olive gray color:

| Variable | Usage |
|----------|-------|
| `--slate-1` | Lightest background |
| `--slate-2` | App background |
| `--slate-3` | Hover states |
| `--slate-4` | Light borders |
| `--slate-6` | Medium borders |
| `--slate-9` | Secondary text |
| `--slate-11` | Primary text |
| `--slate-12` | High contrast text |

Use `--slate-alpha-*` for transparent grays (e.g., `--slate-alpha-3` for hover).

### Spacing Scale

Radix provides `--space-1` through `--space-9`:

```tsx
padding: 'var(--space-2) var(--space-3)'
gap: 'var(--space-2)'
marginBottom: 'var(--space-4)'
```

### Border Radius

| Variable | Usage |
|----------|-------|
| `--radius-1` | Small (inputs, small buttons) |
| `--radius-2` | Medium (cards, buttons) |
| `--radius-3` | Large (modals, panels) |
| `--radius-full` | Pill shape (chips, tags) |

### Font Configuration

```css
.radix-themes {
  --default-font-family: 'Manrope', sans-serif;
}
```

Available weights: 400, 500, 600, 700

---

## Core Radix Components

### Layout Components

```tsx
import { Flex, Box, Grid } from '@radix-ui/themes';

// Flexbox container
<Flex align="center" justify="between" gap="2" direction="column">

// Generic container
<Box style={{ padding: 'var(--space-4)' }}>

// Grid layout
<Grid columns={{ initial: "1", md: "2" }} gap="4">
```

**Flex Props**:
- `align`: `"start"` | `"center"` | `"end"` | `"baseline"` | `"stretch"`
- `justify`: `"start"` | `"center"` | `"end"` | `"between"`
- `direction`: `"row"` | `"column"` | `"row-reverse"` | `"column-reverse"`
- `gap`: `"1"` - `"9"`
- `wrap`: `"wrap"` | `"nowrap"`

### Typography

```tsx
import { Text, Heading } from '@radix-ui/themes';

<Text size="2" color="gray" weight="medium">
  Secondary text
</Text>

<Heading size="5" weight="bold">
  Page Title
</Heading>
```

**Text Props**:
- `size`: `"1"` - `"9"` (1 = smallest)
- `weight`: `"light"` | `"regular"` | `"medium"` | `"bold"`
- `color`: `"gray"` | `"accent"` | custom via style

### Form Components

```tsx
import { Button, Checkbox, Switch } from '@radix-ui/themes';

<Button size="2" variant="solid">
  Submit
</Button>

<Checkbox size="1" checked={isChecked} onCheckedChange={setIsChecked} />

<Switch size="1" checked={isEnabled} onCheckedChange={setIsEnabled} />
```

### Card

```tsx
import { Card } from '@radix-ui/themes';

<Card style={{ padding: 'var(--space-4)' }}>
  Content
</Card>
```

---

## Styling Patterns

### Pattern 1: Inline Styles with CSS Variables

Always use CSS variables for colors, spacing, and radius:

```tsx
<Flex
  style={{
    backgroundColor: 'var(--slate-2)',
    padding: 'var(--space-3)',
    borderRadius: 'var(--radius-2)',
    border: '1px solid var(--slate-6)',
  }}
>
```

### Pattern 2: Interactive Hover States

Use React state to track hover and apply conditional styles:

```tsx
const [isHovered, setIsHovered] = useState(false);

<Flex
  onMouseEnter={() => setIsHovered(true)}
  onMouseLeave={() => setIsHovered(false)}
  style={{
    backgroundColor: isHovered ? 'var(--slate-alpha-3)' : 'transparent',
    cursor: 'pointer',
    transition: 'background-color 0.15s',
  }}
>
```

### Pattern 3: Selected + Hover States

Combine selection state with hover for interactive lists:

```tsx
<Flex
  style={{
    backgroundColor: isSelected
      ? 'var(--accent-3)'
      : isHovered
      ? 'var(--slate-alpha-3)'
      : 'transparent',
  }}
>
```

### Pattern 4: Responsive Props

Use Radix's responsive object syntax:

```tsx
<Grid columns={{ initial: "1", sm: "2", md: "3" }} gap="4">

<Flex direction={{ initial: "column", md: "row" }}>
```

### Pattern 5: Fixed Dimensions

Use inline styles for specific pixel values:

```tsx
<Box style={{ width: '240px', height: '100vh' }}>
```

---

## Creating Custom Components

### IconButton Pattern

Reusable button with icon and hover state:

```tsx
interface IconButtonProps {
  icon: string;
  label?: string;
  onClick: () => void;
}

function IconButton({ icon, label, onClick }: IconButtonProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      gap="1"
      style={{
        padding: '6px 12px',
        cursor: 'pointer',
        backgroundColor: isHovered ? 'var(--slate-alpha-3)' : 'transparent',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--slate-6)',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={onClick}
    >
      <MaterialIcon name={icon} size={16} color="var(--slate-11)" />
      {label && (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {label}
        </Text>
      )}
    </Flex>
  );
}
```

### Filter Chip Pattern

Toggleable chip with active state:

```tsx
interface FilterChipProps {
  label: string;
  isActive: boolean;
  onClick: () => void;
}

function FilterChip({ label, isActive, onClick }: FilterChipProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      style={{
        height: '24px',
        padding: '0 8px',
        cursor: 'pointer',
        backgroundColor: isActive
          ? 'var(--accent-3)'
          : isHovered
          ? 'var(--slate-alpha-3)'
          : 'transparent',
        borderRadius: 'var(--radius-full)',
        border: `1px solid ${isActive ? 'var(--accent-6)' : 'var(--slate-6)'}`,
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={onClick}
    >
      <Text
        size="1"
        style={{ color: isActive ? 'var(--accent-11)' : 'var(--slate-11)' }}
      >
        {label}
      </Text>
    </Flex>
  );
}
```

---

## UI Component Wrappers

### MaterialIcon

**File**: `app/components/ui/MaterialIcon.tsx`

Wrapper for Google Material Icons:

```tsx
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

<MaterialIcon name="search" size={16} color="var(--slate-11)" />
<MaterialIcon name="folder" size={24} color="var(--accent-9)" />
```

**Props**:
- `name`: Material icon name (e.g., `"search"`, `"folder"`, `"close"`)
- `size`: Icon size in pixels (default: 20)
- `color`: CSS color value (default: inherit)
- `style`: Additional inline styles

### Select

**File**: `app/components/ui/Select.tsx`

Re-exports Radix Select with convenient naming:

```tsx
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
} from '@/app/components/ui/Select';

<Select value={value} onValueChange={setValue}>
  <SelectTrigger />
  <SelectContent>
    <SelectItem value="option1">Option 1</SelectItem>
    <SelectItem value="option2">Option 2</SelectItem>
  </SelectContent>
</Select>
```

---

## Quick Reference

### Color Usage Guide

| Use Case | Variable |
|----------|----------|
| App background | `var(--slate-2)` |
| Card/panel background | `var(--slate-1)` |
| Hover background | `var(--slate-alpha-3)` |
| Selected background | `var(--accent-3)` |
| Primary text | `var(--slate-12)` |
| Secondary text | `var(--slate-11)` |
| Muted text | `var(--slate-9)` |
| Primary button | `var(--accent-9)` |
| Primary icon | `var(--accent-9)` |
| Secondary icon | `var(--slate-11)` |
| Border (light) | `var(--slate-4)` |
| Border (medium) | `var(--slate-6)` |

### Allowed className Usage

Only two patterns are valid:

1. `className="material-icons-outlined"` - For Google Material Icons
2. `className="no-scrollbar"` - Hide scrollbars utility

Everything else uses Radix components + inline styles.

### Common Style Snippets

**Card with hover**:
```tsx
style={{
  padding: 'var(--space-4)',
  backgroundColor: isHovered ? 'var(--slate-3)' : 'var(--slate-1)',
  borderRadius: 'var(--radius-2)',
  border: '1px solid var(--slate-4)',
  cursor: 'pointer',
  transition: 'background-color 0.15s',
}}
```

**Full-height sidebar**:
```tsx
style={{
  width: '240px',
  height: '100%',
  backgroundColor: 'var(--slate-1)',
  borderRight: '1px solid var(--slate-4)',
  padding: 'var(--space-2)',
}}
```

**Text truncation**:
```tsx
style={{
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}}
```

**Disabled button**:
```tsx
style={{
  backgroundColor: hasContent ? 'var(--accent-9)' : 'var(--slate-a2)',
  cursor: hasContent ? 'pointer' : 'default',
}}
```

---

## Sidebar Architecture

Navigation sidebars use `SidebarBase` from `@/app/components/sidebar`. Each page co-locates its sidebar in `./sidebar/`. The layout injects sidebars via `@sidebar` parallel route slot (Chat) or inline rendering (KB — pending store migration). Import sizing constants (`SIDEBAR_WIDTH`, `ELEMENT_HEIGHT`, `SECTION_PADDING_TOP`, `SECTION_PADDING_BOTTOM`, `TREE_INDENT_PER_LEVEL`, `TREE_BASE_PADDING`) from `@/app/components/sidebar/constants`. Header and footer slots on `SidebarBase` are optional.
