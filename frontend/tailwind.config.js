/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#18202B",
        label: "#FFFFFF",
        slate: { map: "#E6EAEE", line: "#C9D0D7", mute: "#5C6670" },
        ube: { DEFAULT: "#5B2A86", soft: "#EFE7F6" },
        mango: { DEFAULT: "#F2A900", soft: "#FFF4D6" },
        nodata: "#A3ABB4",
        warn: "#B42318",
      },
      fontFamily: {
        sans: ['"Archivo"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
