'use client';

import React from 'react';

interface PipesHubIconProps {
  size?: number;
  color?: string;
  style?: React.CSSProperties;
  className?: string;
}

export function PipesHubIcon({
  size = 80,
  color = 'currentColor',
  style,
  className,
}: PipesHubIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 80 80"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ color, display: 'inline-flex', flexShrink: 0, ...style }}
      className={className}
    >
      <g filter="url(#pipeshub-icon-shadow)">
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M57.2562 11.7113V23.5185L68.9959 23.5185V11.7113L57.2562 11.7113Z"
          fill="currentColor"
        />
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M57.2562 58.7458V70.553L68.9959 70.553V58.7458H57.2562Z"
          fill="currentColor"
        />
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M11.6439 11.7113V23.5185L23.3835 23.5185L23.3835 11.7113L11.6439 11.7113Z"
          fill="currentColor"
        />
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M11.6439 58.7458V70.553L23.3835 70.553L23.3835 58.7458H11.6439Z"
          fill="currentColor"
        />
        <path
          d="M17.7811 40.6901L8.89057 31.7484L0 40.6901L8.89058 49.6318L17.7811 40.6901Z"
          fill="currentColor"
        />
        <path
          d="M40.4576 62.1166L49.3481 71.0583L40.4576 80L31.567 71.0583L40.4576 62.1166Z"
          fill="currentColor"
        />
        <path
          d="M71.1095 31.7484L80 40.6901L71.1095 49.6318L62.2189 40.6901L71.1095 31.7484Z"
          fill="currentColor"
        />
        <path
          d="M40.4576 0L49.3481 8.9417L40.4576 17.8834L31.567 8.9417L40.4576 0Z"
          fill="currentColor"
        />
        <path
          d="M40.0438 31.7866L48.9343 40.7283L40.0438 49.67L31.1532 40.7283L40.0438 31.7866Z"
          fill="currentColor"
        />
      </g>
      <defs>
        <filter
          id="pipeshub-icon-shadow"
          x="0"
          y="0"
          width="80"
          height="81.5"
          filterUnits="userSpaceOnUse"
          colorInterpolationFilters="sRGB"
        >
          <feFlood floodOpacity="0" result="BackgroundImageFix" />
          <feBlend mode="normal" in="SourceGraphic" in2="BackgroundImageFix" result="shape" />
          <feColorMatrix
            in="SourceAlpha"
            type="matrix"
            values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
            result="hardAlpha"
          />
          <feOffset dy="1.5" />
          <feGaussianBlur stdDeviation="1" />
          <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1" />
          <feColorMatrix
            type="matrix"
            values="0 0 0 0 0.847059 0 0 0 0 0.956863 0 0 0 0 0.964706 0 0 0 0.0352941 0"
          />
          <feBlend mode="normal" in2="shape" result="effect1_innerShadow" />
          <feColorMatrix
            in="SourceAlpha"
            type="matrix"
            values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
            result="hardAlpha"
          />
          <feOffset dy="1.5" />
          <feGaussianBlur stdDeviation="1" />
          <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1" />
          <feColorMatrix type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.1 0" />
          <feBlend mode="normal" in2="effect1_innerShadow" result="effect2_innerShadow" />
        </filter>
      </defs>
    </svg>
  );
}
