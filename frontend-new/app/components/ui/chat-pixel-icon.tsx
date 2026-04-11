'use client';

import React from 'react';

interface ChatPixelIconProps extends React.SVGProps<SVGSVGElement> {
  size?: number;
}

export function ChatPixelIcon({ size = 100, ...props }: ChatPixelIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <path
        d="M62.5 81.25H25V75H62.5V81.25ZM25 75H18.75V68.75H25V75ZM68.75 75H62.5V68.75H68.75V75ZM31.25 68.75H25V62.5H31.25V68.75ZM75 68.75H68.75V62.5H75V68.75ZM25 62.5H18.75V37.5H25V62.5ZM81.25 62.5H75V37.5H81.25V62.5ZM56.25 50V56.25H43.75V50H56.25ZM31.25 37.5H25V31.25H31.25V37.5ZM75 37.5H68.75V31.25H75V37.5ZM37.5 31.25H31.25V25H37.5V31.25ZM68.75 31.25H62.5V25H68.75V31.25ZM62.5 25H37.5V18.75H62.5V25Z"
        fill="currentColor"
      />
    </svg>
  );
}
