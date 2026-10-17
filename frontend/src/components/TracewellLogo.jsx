import React from "react";

/**
 * Tracewell Logo — Modern enterprise incident copilot mark.
 * Features an intelligent connected-node branching constellation forming a geometric "T",
 * symbolizing service relationships, evidence correlation, and root-cause resolution.
 */
export function TracewellLogo({ size = 28, className = "" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`tracewell-logo-svg ${className}`}
      aria-label="Tracewell Logo"
    >
      <defs>
        <linearGradient
          id="tw-logo-grad"
          x1="0"
          y1="0"
          x2="32"
          y2="32"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#1d4ed8" />
        </linearGradient>
      </defs>

      {/* Branded rounded container */}
      <rect width="32" height="32" rx="7" fill="url(#tw-logo-grad)" />

      {/* Network branches forming the investigative T constellation */}
      {/* Primary horizontal bar: telemetry inputs */}
      <line
        x1="7.5"
        y1="9.5"
        x2="24.5"
        y2="9.5"
        stroke="#ffffff"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeOpacity="0.9"
      />
      {/* Primary vertical stem: causal chain */}
      <line
        x1="16"
        y1="9.5"
        x2="16"
        y2="23.5"
        stroke="#ffffff"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeOpacity="0.9"
      />
      {/* Correlation branch: evidence diagnostic link */}
      <line
        x1="16"
        y1="16.5"
        x2="22.5"
        y2="21"
        stroke="#bfdbfe"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeDasharray="2.2 1.8"
      />

      {/* Connected nodes */}
      <circle cx="7.5" cy="9.5" r="2.4" fill="#ffffff" />
      <circle cx="24.5" cy="9.5" r="2.4" fill="#ffffff" />

      {/* Center hub node */}
      <circle cx="16" cy="9.5" r="3.2" fill="#ffffff" />
      <circle cx="16" cy="9.5" r="1.4" fill="#2563eb" />

      {/* Intermediate reasoning node */}
      <circle cx="16" cy="16.5" r="1.9" fill="#bfdbfe" />

      {/* Resolution target node */}
      <circle cx="16" cy="23.5" r="2.4" fill="#ffffff" />

      {/* Correlated evidence node */}
      <circle cx="22.5" cy="21" r="2" fill="#93c5fd" />
    </svg>
  );
}

export default TracewellLogo;
