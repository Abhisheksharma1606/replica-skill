export function money(amount: number, currency: string, opts: { sign?: boolean } = {}) {
  let text: string;
  try {
    text = new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Math.abs(amount));
  } catch {
    text = `${currency} ${Math.abs(amount).toFixed(2)}`;
  }
  if (amount < 0) return `-${text}`;
  return opts.sign && amount > 0 ? `+${text}` : text;
}

export function todayISO(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function monthKey(iso: string) {
  return iso.slice(0, 7);
}

export function prettyDate(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

export const CATEGORY_ICONS: Record<string, string> = {
  Groceries: "🛒",
  Dining: "🍽️",
  Transport: "🚌",
  Housing: "🏠",
  Utilities: "💡",
  Shopping: "🛍️",
  Health: "🩺",
  Entertainment: "🎬",
  Travel: "✈️",
  Education: "📚",
  Subscriptions: "🔁",
  Income: "💰",
  Transfer: "🔄",
  Other: "📦",
};
