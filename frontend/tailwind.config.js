/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bkash: "#E2136E",
        nagad: "#F6921E",
        rocket: "#8C3494",
      },
      keyframes: {
        "pulse-warn": {
          "0%,100%": { boxShadow: "0 0 0 0 rgba(245,158,11,0.7)" },
          "50%":     { boxShadow: "0 0 0 6px rgba(245,158,11,0.0)" },
        },
      },
      animation: {
        "pulse-warn": "pulse-warn 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};