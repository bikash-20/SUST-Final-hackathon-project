/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Palette is sourced from CSS variables so the same class names
        // render correctly on the previous white look and the current
        // dark "trading terminal" look. `html.dark` overrides in
        // globals.css flip the values.
        base: "var(--color-base)",
        surface: "var(--color-surface)",
        "surface-2": "var(--color-surface-2)",
        border: "var(--color-border)",
        ink: "var(--color-ink)",
        muted: "var(--color-muted)",
        signal: "var(--color-signal)",
        "signal-soft": "var(--color-signal-soft)",
        // Provider identity is theme-agnostic — the desaturated hex
        // values read cleanly on white and on dark.
        bkash: "#E0447A",
        nagad: "#E0883B",
        rocket: "#8B7FE8",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: [
          "var(--font-plex-mono)",
          "ui-monospace",
          "SFMono-Regular",
          "monospace",
        ],
      },
      letterSpacing: {
        eyebrow: "0.14em",
      },
      boxShadow: {
        card: "0 1px 0 rgba(255,255,255,0.02) inset, 0 8px 24px rgba(0,0,0,0.35)",
        "card-light":
          "0 1px 0 rgba(255,255,255,0.6) inset, 0 8px 24px rgba(15,23,42,0.08)",
      },
      keyframes: {
        "pulse-live": {
          "0%,100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.4", transform: "scale(0.85)" },
        },
        "pulse-warn": {
          "0%,100%": { boxShadow: "0 0 0 0 rgba(240,169,59,0.55)" },
          "50%": { boxShadow: "0 0 0 7px rgba(240,169,59,0.0)" },
        },
      },
      animation: {
        "pulse-live": "pulse-live 1.1s ease-in-out infinite",
        "pulse-warn": "pulse-warn 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};