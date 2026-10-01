import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        cenomi: {
          gold: "#C9A84C",
          dark: "#1A1A2E",
        },
      },
    },
  },
  plugins: [],
};

export default config;
