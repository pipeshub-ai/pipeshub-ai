"use client"

import { Select as RadixSelect } from "@radix-ui/themes"

// Re-export Radix Themes Select components with our naming convention
const Select = RadixSelect.Root
const SelectTrigger = RadixSelect.Trigger
const SelectContent = RadixSelect.Content
const SelectGroup = RadixSelect.Group
const SelectItem = RadixSelect.Item
const SelectLabel = RadixSelect.Label
const SelectSeparator = RadixSelect.Separator

// For backwards compatibility with SelectValue usage
const SelectValue = ({ placeholder: _placeholder }: { placeholder?: string }) => {
  // Radix Themes handles this internally via the placeholder prop on Trigger
  return null
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
}
