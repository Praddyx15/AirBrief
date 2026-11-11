import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";

type TailwindPlugin = NonNullable<Config["plugins"]>[number];

// CommonJS plugin; required so the config does not depend on its type declarations.
const animate = require("tailwindcss-animate") as TailwindPlugin;

/**
 * Colours map to the semantic CSS variables in app/globals.css (PLAN §7).
 * Each token exists twice: `--name` (exact hex, for CSS and canvas) and `--name-rgb`
 * (space-separated channels) so Tailwind opacity modifiers such as `bg-accent/10` work.
 */
const token = (name: string) => `rgb(var(--${name}-rgb) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      // 16 px gutters on phones, more breathing room on wide screens.
      padding: { DEFAULT: "1rem", lg: "2rem", "2xl": "3rem" },
      screens: { "2xl": "1440px" },
    },
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        elevated: token("elevated"),
        border: token("border"),
        fg: {
          DEFAULT: token("text"),
          muted: token("text-muted"),
          subtle: token("text-subtle"),
        },
        accent: {
          DEFAULT: token("accent"),
          solid: token("accent-solid"),
          text: token("accent-text"),
          foreground: token("on-accent"),
        },
        ok: token("ok"),
        warn: token("warn"),
        danger: {
          DEFAULT: token("danger"),
          solid: token("danger-solid"),
        },
        zone: {
          green: token("zone-green"),
          yellow: token("zone-yellow"),
          red: token("zone-red"),
        },
        ring: token("accent"),
      },
      borderColor: {
        DEFAULT: token("border"),
      },
      borderRadius: {
        DEFAULT: "6px",
        sm: "4px",
        md: "6px",
        lg: "8px",
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      spacing: {
        rail: "56px",
        "panel-left": "300px",
        "panel-right": "340px",
      },
      transitionDuration: {
        DEFAULT: "200ms",
        "250": "250ms",
      },
      transitionTimingFunction: {
        DEFAULT: "cubic-bezier(0.2, 0, 0, 1)",
      },
      zIndex: {
        map: "0",
        panel: "10",
        drawer: "20",
        overlay: "40",
        modal: "50",
        toast: "60",
      },
      keyframes: {
        "progress-indeterminate": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(250%)" },
        },
      },
      animation: {
        "progress-indeterminate": "progress-indeterminate 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [
    animate,
    plugin(({ addVariant }) => {
      // Touch devices: enlarge targets to >= 44 px (PLAN §7 interaction rules).
      addVariant("pointer-coarse", "@media (pointer: coarse)");
      addVariant("pointer-fine", "@media (pointer: fine)");
      // In-app reduced-motion override (set on <html data-motion="reduce">).
      addVariant("reduce-motion", [
        "html[data-motion=reduce] &",
        "@media (prefers-reduced-motion: reduce) { html:not([data-motion=full]) & }",
      ]);
    }),
  ],
};

export default config;
