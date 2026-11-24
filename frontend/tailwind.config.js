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
        mango: { DEFAULT: "#F7C15A", soft: "rgb(var(--accent-soft) / <alpha-value>)" },
        nodata: "rgb(var(--muted) / <alpha-value>)",
        warn: "rgb(var(--danger) / <alpha-value>)",
      },
      fontFamily: {
        sans: ['"Archivo"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
