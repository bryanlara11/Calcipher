/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "rgb(var(--ink) / <alpha-value>)",
        label: "rgb(var(--surface) / <alpha-value>)",
        slate: { map: "rgb(var(--canvas) / <alpha-value>)", line: "rgb(var(--line) / <alpha-value>)", mute: "rgb(var(--muted) / <alpha-value>)" },
        ube: { DEFAULT: "rgb(var(--primary) / <alpha-value>)", soft: "rgb(var(--soft) / <alpha-value>)" },
        mango: { DEFAULT: "#5B8219", soft: "rgb(var(--accent-soft) / <alpha-value>)" },
        nodata: "rgb(var(--muted) / <alpha-value>)",
        warn: "rgb(var(--danger) / <alpha-value>)",
      },
      fontFamily: {
        sans: ['"Figtree Variable"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Arial', 'sans-serif'],
      },
      fontSize: {
        xs: ["var(--text-small)", { lineHeight: "1.6" }],
        sm: ["var(--text-small)", { lineHeight: "1.6" }],
        base: ["var(--text-body)", { lineHeight: "1.6" }],
        lg: ["var(--text-subheading)", { lineHeight: "1.4" }],
        xl: ["var(--text-heading)", { lineHeight: "1.4" }],
      },
    },
  },
  plugins: [],
};
