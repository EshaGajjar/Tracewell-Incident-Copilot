import { useState, useMemo } from "react";
import { CopyIcon, CheckIcon } from "./Icons.jsx";

export function formatJsonPayload(raw) {
  if (raw === undefined) return { text: "undefined", isJson: false };
  if (raw === null) return { text: "null", isJson: true };
  if (typeof raw === "object") {
    try {
      return { text: JSON.stringify(raw, null, 2), isJson: true };
    } catch {
      return { text: String(raw), isJson: false };
    }
  }
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        const parsed = JSON.parse(trimmed);
        return { text: JSON.stringify(parsed, null, 2), isJson: true };
      } catch {
        return { text: raw, isJson: false };
      }
    }
    return { text: raw, isJson: false };
  }
  return { text: String(raw), isJson: false };
}

export function highlightJsonTokens(jsonText) {
  if (!jsonText || typeof jsonText !== "string") {
    return [String(jsonText)];
  }

  // Regex to match JSON tokens:
  // 1: Keys or string values (quoted strings, optionally followed by colon)
  // 2: colon match if key
  // 3: Booleans
  // 4: Null
  // 5: Numbers
  // 6: Punctuation brackets, braces, and commas
  const tokenRegex =
    /("(?:\\u[0-9a-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?)|(\btrue\b|\bfalse\b)|(\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([\[\]{},:])/g;

  const elements = [];
  let lastIndex = 0;
  let match;
  let keyCounter = 0;

  while ((match = tokenRegex.exec(jsonText)) !== null) {
    if (match.index > lastIndex) {
      elements.push(jsonText.substring(lastIndex, match.index));
    }

    const token = match[0];
    if (match[1]) {
      if (match[2]) {
        // Key with colon
        const colonIdx = token.lastIndexOf(":");
        const keyText = token.slice(0, colonIdx);
        const colonText = token.slice(colonIdx);
        elements.push(
          <span key={`k-${keyCounter++}`} className="json-key">
            {keyText}
          </span>,
          <span key={`p-${keyCounter++}`} className="json-punct">
            {colonText}
          </span>
        );
      } else {
        // String value
        elements.push(
          <span key={`s-${keyCounter++}`} className="json-string">
            {token}
          </span>
        );
      }
    } else if (match[3]) {
      elements.push(
        <span key={`b-${keyCounter++}`} className="json-boolean">
          {token}
        </span>
      );
    } else if (match[4]) {
      elements.push(
        <span key={`n-${keyCounter++}`} className="json-null">
          {token}
        </span>
      );
    } else if (match[5]) {
      elements.push(
        <span key={`num-${keyCounter++}`} className="json-number">
          {token}
        </span>
      );
    } else if (match[6]) {
      elements.push(
        <span key={`punc-${keyCounter++}`} className="json-punct">
          {token}
        </span>
      );
    }

    lastIndex = tokenRegex.lastIndex;
  }

  if (lastIndex < jsonText.length) {
    elements.push(jsonText.substring(lastIndex));
  }

  return elements;
}

export default function JsonCodeBlock({ data, label = "payload" }) {
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);

  const { text, isJson } = useMemo(() => formatJsonPayload(data), [data]);

  const tokens = useMemo(() => highlightJsonTokens(text), [text]);

  const lineCount = useMemo(() => text.split("\n").length, [text]);
  const byteCount = useMemo(() => new Blob([text]).size, [text]);
  const sizeLabel = useMemo(() => {
    if (byteCount < 1024) return `${byteCount} B`;
    return `${(byteCount / 1024).toFixed(1)} KB`;
  }, [byteCount]);

  async function handleCopy(e) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  }

  return (
    <div className="json-code-block-container">
      <div className="json-code-block-toolbar">
        <div className="json-code-block-meta">
          <span className="json-format-tag">{isJson ? "JSON" : "TEXT"}</span>
          <span className="json-meta-stat">
            {lineCount} {lineCount === 1 ? "line" : "lines"}
          </span>
          <span className="json-meta-stat">{sizeLabel}</span>
        </div>
        <div className="json-code-block-controls">
          <button
            type="button"
            className={`json-control-btn ${wrap ? "active" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setWrap((w) => !w);
            }}
            title={wrap ? "Disable word wrap (scroll horizontally)" : "Enable word wrap"}
          >
            {wrap ? "Scroll" : "Wrap"}
          </button>
          <button
            type="button"
            className={`json-control-btn copy-btn ${copied ? "copied" : ""}`}
            onClick={handleCopy}
            title={`Copy ${label} to clipboard`}
          >
            {copied ? (
              <>
                <CheckIcon size={12} />
                <span>Copied</span>
              </>
            ) : (
              <>
                <CopyIcon size={12} />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      </div>
      <pre className={`json-code-pre ${wrap ? "wrap-mode" : "scroll-mode"}`} tabIndex={0}>
        <code>{tokens}</code>
      </pre>
    </div>
  );
}
