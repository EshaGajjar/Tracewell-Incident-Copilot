import React, { useState, useEffect } from "react";
import TracewellLogo from "./TracewellLogo.jsx";

/**
 * Branded Website Launch Experience
 * - Minimal, premium launch screen using Tracewell branding.
 * - Displays Tracewell logo, "TRACEWELL", subtitle "Incident Copilot",
 *   and tagline: "Investigate. Understand. Resolve."
 * - Subtle animated connected-node/network visual representing service relationships and incident diagnosis.
 * - Shown only while the application is initially loading.
 * - Never forces artificial delays when data is already ready.
 * - Skipped on subsequent in-session navigations.
 */
export function LaunchScreen({ isReady = false, onComplete }) {
  // Check if launch screen was already presented in this browser session
  const [alreadyShown] = useState(() => {
    try {
      return sessionStorage.getItem("tracewell_launch_shown") === "1";
    } catch {
      return false;
    }
  });

  // If already shown or already ready on initial mount, do not show
  const [visible, setVisible] = useState(() => {
    if (alreadyShown || isReady) {
      return false;
    }
    return true;
  });

  const [isExiting, setIsExiting] = useState(false);

  useEffect(() => {
    if (!visible) return;

    if (isReady) {
      // Trigger smooth fade-out immediately without artificial delay
      setIsExiting(true);
      const timer = setTimeout(() => {
        setVisible(false);
        try {
          sessionStorage.setItem("tracewell_launch_shown", "1");
        } catch {}
        if (onComplete) onComplete();
      }, 350);

      return () => clearTimeout(timer);
    }
  }, [isReady, visible, onComplete]);

  if (!visible) return null;

  return (
    <div
      className={`launch-screen ${isExiting ? "is-exiting" : ""}`}
      role="status"
      aria-live="polite"
      aria-label="Loading Tracewell Incident Copilot"
    >
      <div className="launch-ambient-glow" aria-hidden="true" />

      <div className="launch-container">
        {/* Animated Connected-Node / Network Visual */}
        <div className="launch-network-wrap" aria-hidden="true">
          <svg
            className="launch-network-svg"
            viewBox="0 0 240 140"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            {/* Background connection links */}
            <g className="network-edges">
              <line x1="40" y1="70" x2="90" y2="35" className="net-edge net-edge-flow" />
              <line x1="40" y1="70" x2="90" y2="105" className="net-edge" />
              <line x1="90" y1="35" x2="150" y2="45" className="net-edge net-edge-flow" />
              <line x1="90" y1="35" x2="120" y2="70" className="net-edge" />
              <line x1="90" y1="105" x2="120" y2="70" className="net-edge net-edge-flow" />
              <line x1="90" y1="105" x2="150" y2="95" className="net-edge" />
              <line x1="120" y1="70" x2="185" y2="70" className="net-edge net-edge-pulse" />
              <line x1="150" y1="45" x2="200" y2="40" className="net-edge" />
              <line x1="150" y1="95" x2="200" y2="100" className="net-edge net-edge-flow" />
              <line x1="185" y1="70" x2="200" y2="40" className="net-edge" />
              <line x1="185" y1="70" x2="200" y2="100" className="net-edge" />
            </g>

            {/* Radar Pulse on Central Diagnostic Node */}
            <circle cx="120" cy="70" r="16" className="net-radar-pulse" />

            {/* Connected Nodes */}
            <g className="network-nodes">
              {/* Ingress node */}
              <circle cx="40" cy="70" r="4.5" className="net-node node-tier-1" />
              {/* Mid-tier services */}
              <circle cx="90" cy="35" r="4" className="net-node node-tier-2" />
              <circle cx="90" cy="105" r="4" className="net-node node-tier-2" />
              {/* Investigation Correlation Hub */}
              <circle cx="120" cy="70" r="6" className="net-node node-hub" />
              <circle cx="120" cy="70" r="2.2" className="net-node-core" />
              {/* Downstream services */}
              <circle cx="150" cy="45" r="4" className="net-node node-tier-2" />
              <circle cx="150" cy="95" r="4" className="net-node node-tier-2" />
              {/* Data / Destination targets */}
              <circle cx="185" cy="70" r="5" className="net-node node-tier-3" />
              <circle cx="200" cy="40" r="3.5" className="net-node node-tier-3" />
              <circle cx="200" cy="100" r="3.5" className="net-node node-tier-3" />
            </g>
          </svg>
        </div>

        {/* Brand Identity & Header */}
        <div className="launch-brand-group">
          <div className="launch-logo-box">
            <TracewellLogo size={46} className="launch-logo-svg" />
          </div>
          <div className="launch-brand-title">TRACEWELL</div>
          <div className="launch-brand-subtitle">Incident Copilot</div>
        </div>

        {/* Tagline */}
        <div className="launch-tagline">Investigate. Understand. Resolve.</div>

        {/* Indeterminate truthful status */}
        <div className="launch-status-pill">
          <span className="launch-pulse-dot" />
          <span className="launch-status-text">Connecting telemetry & service topology…</span>
        </div>
      </div>
    </div>
  );
}

export default LaunchScreen;
