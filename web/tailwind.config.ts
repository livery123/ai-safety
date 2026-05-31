/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eff6ff",
          600: "#2563eb",
          700: "#1d4ed8",
        },
        policy: "#2563eb",
        meeting: "#7c3aed",
        literature: "#059669",
      },
      boxShadow: {
        card: "0 8px 30px rgba(15, 23, 42, 0.08)",
      },
    },
  },
  plugins: [],
};
