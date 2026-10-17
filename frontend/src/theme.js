import { useState, useEffect } from "react";

const THEME_STORAGE_KEY = "tracewell-theme";

/**
 * Returns the stored theme mode: 'dark' | 'light'
 * Defaults to 'dark' if none selected or if previously set to an invalid value.
 */
export function getSavedTheme() {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

/**
 * Resolves theme ('dark' | 'light').
 */
export function resolveTheme(preference = getSavedTheme()) {
  return preference === "light" ? "light" : "dark";
}

/**
 * Apply theme to document element and meta tag
 */
export function applyTheme(preference) {
  const effective = resolveTheme(preference);
  const root = document.documentElement;

  root.setAttribute("data-theme", effective);
  root.setAttribute("data-color-mode", effective);

  let meta = document.querySelector('meta[name="color-scheme"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "color-scheme";
    document.head.appendChild(meta);
  }
  meta.content = effective;

  try {
    localStorage.setItem(THEME_STORAGE_KEY, effective);
  } catch {
    // ignore in restricted mode
  }

  // Dispatch custom event so listeners know theme resolved
  window.dispatchEvent(
    new CustomEvent("tracewell-theme-changed", {
      detail: { preference: effective, effective },
    })
  );

  return effective;
}

/**
 * React hook to manage theme across components
 */
export function useTheme() {
  const [theme, setThemeState] = useState(getSavedTheme);

  const setTheme = (newTheme) => {
    const target = newTheme === "light" ? "light" : "dark";
    setThemeState(target);
    applyTheme(target);
  };

  useEffect(() => {
    // Initial sync
    const eff = applyTheme(theme);
    setThemeState(eff);

    // Listen for cross-tab storage changes
    const handleStorageChange = (e) => {
      if (e.key === THEME_STORAGE_KEY) {
        const nextPref = e.newValue === "light" ? "light" : "dark";
        setThemeState(nextPref);
        applyTheme(nextPref);
      }
    };
    window.addEventListener("storage", handleStorageChange);

    return () => {
      window.removeEventListener("storage", handleStorageChange);
    };
  }, []);

  return { theme, effectiveTheme: theme, setTheme };
}

