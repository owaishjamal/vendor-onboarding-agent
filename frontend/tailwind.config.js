/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Zamp's dual typeface system. Existing fallbacks are kept so nothing
        // breaks on a machine without Geist installed.
        sans: ["Geist", "ui-sans-serif", "system-ui", "-apple-system",
               "Segoe UI", "Roboto", "sans-serif"],
        geist: ["Geist", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "Menlo",
               "Monaco", "monospace"],
        ui: ["ui-sans-serif", "system-ui", "sans-serif"],
      },
      letterSpacing: {
        // The aggressive negative tracking that defines the Zamp voice.
        tightest: "-0.04em",
        wordmark: "-14.8px",
        label: "-0.02em",
      },
      colors: {
        // Zamp Blue. Kept under the existing `brand` key so no component has
        // to change, but used sparingly — the system is monochrome-first and
        // this is reserved for the single most important action on a screen.
        brand: {
          50: "#e6efff",
          100: "#cfe0ff",
          400: "#3d85ff",
          500: "#005eff",
          600: "#0050d9",
          700: "#0041b0",
        },
        zamp: {
          blue: "#005eff",
          black: "#000000",
          charcoal: "#302f37",
          "near-black": "#1e1e1e",
          "surface-gray": "#efefef",
          "warm-off-white": "#f5f5f5",
          "light-warm-gray": "#f0edea",
          "cream-border": "#e7e2df",
        },
        // Monochrome / warm-gray surfaces, mapped onto the existing scale.
        surface: {
          0: "#ffffff",
          50: "#f5f5f5",
          100: "#efefef",
          200: "#e7e2df",
          300: "#d8d4d1",
          500: "#6f6d75",
          700: "#302f37",
          900: "#1e1e1e",
        },
        accent: {
          500: "#1e1e1e",
          900: "#000000",
        },
        warn: {
          50: "#FFF8E6",
          100: "#FCE9B8",
          400: "#F0B429",
          500: "#DB960E",
          700: "#8A5D00",
        },
        danger: {
          50: "#FDECEC",
          100: "#F8C6C6",
          400: "#EF5350",
          500: "#E63A36",
          700: "#A01D1D",
        },
      },
      boxShadow: {
        card: "none",
        elevated: "none",
      },
      borderRadius: {
        "radius-sm": "4px", "radius-md": "6px", "radius-base": "10px",
        "radius-card": "12px", "radius-lg": "15px", "radius-xl": "16px",
        "radius-2xl": "20px", "radius-pill": "9999px",
      },
      spacing: {
        "space-1": "4px", "space-2": "8px", "space-3": "12px",
        "space-4": "16px", "space-5": "20px", "space-6": "24px",
        "space-7": "28px", "space-8": "32px", "space-9": "36px",
        "space-10": "40px", "space-12": "48px", "space-18": "72px",
        "space-24": "96px", "space-32": "128px", "space-45": "180px",
      },
    },
  },
  plugins: [],
};
