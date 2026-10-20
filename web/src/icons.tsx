type IconProps = { size?: number };

function base(size: number) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };
}

export function IconSettings({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M5.8 5.8l1.6 1.6M16.6 16.6l1.6 1.6M18.2 5.8l-1.6 1.6M7.4 16.6l-1.6 1.6" />
    </svg>
  );
}

export function IconLocate({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="3.25" />
      <circle cx="12" cy="12" r="7.25" />
      <path d="M12 2.75v2M12 19.25v2M2.75 12h2M19.25 12h2" />
    </svg>
  );
}

export function IconPlus({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M12 6v12M6 12h12" />
    </svg>
  );
}

export function IconMinus({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M6 12h12" />
    </svg>
  );
}

export function IconFit({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M9 4.5H5.5A1 1 0 0 0 4.5 5.5V9M15 4.5h3.5a1 1 0 0 1 1 1V9M9 19.5H5.5a1 1 0 0 1-1-1V15M15 19.5h3.5a1 1 0 0 0 1-1V15" />
    </svg>
  );
}

export function IconFaceId({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M8 4.75H6.4A1.65 1.65 0 0 0 4.75 6.4V8" />
      <path d="M16 4.75h1.6a1.65 1.65 0 0 1 1.65 1.65V8" />
      <path d="M8 19.25H6.4a1.65 1.65 0 0 1-1.65-1.65V16" />
      <path d="M16 19.25h1.6a1.65 1.65 0 0 0 1.65-1.65V16" />
      <path d="M9 9.25v1.7M15 9.25v1.7" />
      <path d="M12 9.6v3.1c0 .7-.45 1.15-1.15 1.15" />
      <path d="M9.15 15.7c.8.85 1.7 1.25 2.85 1.25s2.05-.4 2.85-1.25" />
    </svg>
  );
}

export function IconLive({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="2.1" fill="currentColor" stroke="none" />
      <path d="M8.2 8.4a5.2 5.2 0 0 0 0 7.2M15.8 8.4a5.2 5.2 0 0 1 0 7.2M5.6 5.8a8.6 8.6 0 0 0 0 12.4M18.4 5.8a8.6 8.6 0 0 1 0 12.4" />
    </svg>
  );
}

export function IconStop({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="8" />
      <rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconBell({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M6.2 16.5h11.6c-.7-1.2-1.3-2.2-1.3-5.1 0-2.7-1.6-4.6-3.6-5.1V6a.9.9 0 1 0-1.8 0v.3c-2 .5-3.6 2.4-3.6 5.1 0 2.9-.6 3.9-1.3 5.1Z" />
      <path d="M10 16.6a2 2 0 0 0 4 0" />
    </svg>
  );
}

export function IconLight({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 4.2v1.6M12 18.2v1.6M4.2 12h1.6M18.2 12h1.6M6.4 6.4l1.1 1.1M16.5 16.5l1.1 1.1M17.6 6.4l-1.1 1.1M7.5 16.5l-1.1 1.1" />
    </svg>
  );
}

export function IconLightOff({ size = 22 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M9.2 14.6A3.6 3.6 0 0 1 12 8.4" />
      <path d="M14.8 9.4A3.6 3.6 0 0 1 12 15.6" />
      <path d="M12 4.2v1.6M12 18.2v1.6M4.2 12h1.6M18.2 12h1.6" />
      <path d="M5 5l14 14" />
    </svg>
  );
}

export function IconBolt({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)} strokeWidth={1.6}>
      <path d="M13 3.5 6.5 13h4L10 20.5 16.5 11h-4L13 3.5Z" fill="currentColor" stroke="none" />
    </svg>
  );
}
