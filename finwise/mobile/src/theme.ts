import { useColorScheme } from "react-native";

const light = {
  bg: "#F5F7FB",
  card: "#FFFFFF",
  text: "#111827",
  muted: "#6B7280",
  border: "#E5E7EB",
  primary: "#2563EB",
  primaryText: "#FFFFFF",
  positive: "#15803D",
  negative: "#B91C1C",
  warning: "#B45309",
  soft: "#EEF2FF",
};

const dark: typeof light = {
  bg: "#0B1020",
  card: "#151B2E",
  text: "#F3F4F6",
  muted: "#9CA3AF",
  border: "#273049",
  primary: "#60A5FA",
  primaryText: "#0B1020",
  positive: "#4ADE80",
  negative: "#F87171",
  warning: "#FBBF24",
  soft: "#1E2A4A",
};

export type Theme = typeof light;

export function useTheme(): Theme {
  return useColorScheme() === "dark" ? dark : light;
}
