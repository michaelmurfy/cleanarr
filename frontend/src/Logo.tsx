export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect width="64" height="64" rx="16" fill="#1a130c" />
      <rect x="1.2" y="1.2" width="61.6" height="61.6" rx="14.8" stroke="#d4a054" strokeOpacity="0.45" />
      <path
        d="M44 18.5c-2.2-2.4-6.3-4.5-12.2-4.5-11.2 0-18.3 7.4-18.3 18s7.1 18 18.3 18c5.9 0 10-2.1 12.2-4.5"
        stroke="#d4a054"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <rect x="41.5" y="20" width="7.5" height="5.5" rx="1.2" fill="#f0d2a0" />
      <rect x="43" y="29.25" width="7.5" height="5.5" rx="1.2" fill="#d4a054" />
      <rect x="41.5" y="38.5" width="7.5" height="5.5" rx="1.2" fill="#f0d2a0" />
      <path d="M16 16.5l1.1 2.6 2.6 1.1-2.6 1.1L16 23.9l-1.1-2.6-2.6-1.1 2.6-1.1L16 16.5z" fill="#f0d2a0" />
    </svg>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "compact" : ""}`}>
      <Logo size={compact ? 28 : 36} />
      <span className="wordmark">
        Clean<span>arr</span>
      </span>
    </div>
  );
}
