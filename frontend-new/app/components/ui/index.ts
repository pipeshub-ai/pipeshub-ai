/**
 * Shared UI Components
 *
 * This barrel export provides access to all reusable UI components.
 * These components are designed to be used across multiple pages.
 *
 * @example
 * ```tsx
 * import { FilterDropdown, DateRangePicker, MaterialIcon } from '@/app/components/ui';
 * ```
 */

export { MaterialIcon } from './MaterialIcon';
export { ConnectorIcon } from './ConnectorIcon';
export type { ConnectorType } from './ConnectorIcon';
export { Select } from './Select';
export { FileIcon } from './file-icon';
export { FileTypeIcon, FILE_TYPE_ICON_MAP } from './file-type-icons';
export type { FileTypeIconComponentProps } from './file-type-icons';
export { FolderIcon } from './folder-icon';
export type { FolderIconVariant } from './folder-icon';
export { PipesHubIcon } from './pipes-hub-icon';
export { KnowledgeItemIcon } from './knowledge-item-icon';
export { UserAvatar } from './user-avatar';
export type { UserAvatarProps, UserAvatarProfile } from './user-avatar';
export type { KnowledgeItemKind } from './knowledge-item-icon';
export { FilterDropdown } from './filter-dropdown';
export type { FilterOption, FilterDropdownProps } from './filter-dropdown';
export { DateRangePicker } from './date-range-picker';
export type { DateRangePickerProps, DateFilterType } from './date-range-picker';
export { AlertSquareIcon } from './alert-square-icon';
export { NotFoundIcon } from './not-found-icon';
export { EmptyIcon } from './empty-icon';
