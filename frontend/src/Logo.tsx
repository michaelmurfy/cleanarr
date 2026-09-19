export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
      className="logo-mark"
    >
      <defs>
        <linearGradient id="cleanarr-tile" x1="8" y1="4" x2="56" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#261c11" />
          <stop offset="1" stopColor="#11100c" />
        </linearGradient>
        <linearGradient id="cleanarr-sweep" x1="16" y1="16" x2="46" y2="48" gradientUnits="userSpaceOnUse">
          <stop stopColor="#f6dcae" />
          <stop offset="1" stopColor="#c78f3f" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#cleanarr-tile)" />
      <rect x="1" y="1" width="62" height="62" rx="15" stroke="#d4a054" strokeOpacity="0.32" />
      <path
        d="M37.9 17A16.4 16.4 0 1 0 37.9 47"
        stroke="url(#cleanarr-sweep)"
        strokeWidth="6"
        strokeLinecap="round"
      />
      <rect x="35" y="20.5" width="14" height="6" rx="3" fill="#f6dcae" />
      <rect x="35" y="29" width="10.5" height="6" rx="3" fill="#d4a054" />
      <rect x="35" y="37.5" width="7" height="6" rx="3" fill="#d4a054" fillOpacity="0.55" />
    </svg>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "compact" : ""}`}>
      <Logo size={compact ? 30 : 40} />
      <span className="wordmark">
        Clean<span>arr</span>
      </span>
    </div>
  );
}
