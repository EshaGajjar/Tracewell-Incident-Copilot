import React from "react";
import { AlertCircleIcon, RefreshCwIcon } from "./Icons.jsx";

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an unhandled error:", error, errorInfo);
    this.setState({ error, errorInfo });
    this.props.onError?.(error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      if (typeof this.props.fallback === "function") {
        return this.props.fallback({
          error: this.state.error,
          resetError: this.handleReset,
        });
      }

      return (
        <section
          className="card"
          style={{
            borderLeft: "4px solid var(--status-error)",
            padding: "24px",
            margin: "20px 0",
          }}
          role="alert"
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              marginBottom: "12px",
            }}
          >
            <AlertCircleIcon size={22} style={{ color: "var(--status-error)" }} />
            <h3 style={{ margin: 0, color: "var(--text-primary)", fontSize: "18px" }}>
              {this.props.title || "Unable to Display View"}
            </h3>
          </div>

          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginBottom: "16px", lineHeight: "1.5" }}>
            {this.state.error?.message || "An unexpected rendering error occurred while loading this view."}
          </p>

          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginTop: "16px" }}>
            <button className="primary-button" onClick={this.handleReset}>
              <RefreshCwIcon size={14} />
              <span>Try Again</span>
            </button>
            {this.props.onBack && (
              <button className="secondary-button" onClick={this.props.onBack}>
                <span>← Back to Overview</span>
              </button>
            )}
          </div>
        </section>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
