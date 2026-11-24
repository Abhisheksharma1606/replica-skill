import { guessCategory, parseLocally } from "./localAI";
import { money } from "./format";
import type { ChatMessage, Insight, Settings } from "./types";

export class AIError extends Error {}

export function aiEnabled(settings: Settings) {
  return settings.aiServerUrl.trim().length > 0;
}

async function post<T>(settings: Settings, path: string, body: unknown): Promise<T> {
  const base = settings.aiServerUrl.trim().replace(/\/+$/, "");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90_000);
  try {
    const res = await fetch(`${base}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(settings.appToken ? { "x-finwise-token": settings.appToken } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new AIError((json as { error?: string }).error ?? `AI server error (${res.status})`);
    return json as T;
  } catch (e) {
    if (e instanceof AIError) throw e;
    throw new AIError("Couldn't reach the AI server. Check the address in Settings.");
  } finally {
    clearTimeout(timer);
  }
}

export interface ParsedTx {
  description: string;
  amount: number;
  category: string;
  date: string;
  accountName: string | null;
}

export async function parseTransaction(
  settings: Settings,
  text: string,
  today: string,
  categories: readonly string[],
  accounts: string[],
): Promise<ParsedTx> {
  if (!aiEnabled(settings)) return parseLocally(text);
  return post<ParsedTx>(settings, "/api/parse", { text, today, categories, accounts });
}

export async function categorize(
  settings: Settings,
  transactions: { id: string; description: string; amount: number }[],
  categories: readonly string[],
): Promise<{ id: string; category: string; merchant?: string }[]> {
  if (!aiEnabled(settings)) {
    return transactions.map((t) => ({ id: t.id, category: guessCategory(t.description, t.amount) }));
  }
  const out = await post<{ results: { id: string; category: string; merchant: string }[] }>(settings, "/api/categorize", {
    transactions,
    categories,
  });
  return out.results;
}

export async function getInsights(settings: Settings, snapshot: Snapshot): Promise<Insight> {
  if (!aiEnabled(settings)) return localInsights(snapshot);
  const out = await post<Omit<Insight, "generatedAt">>(settings, "/api/insights", { snapshot });
  return { ...out, generatedAt: new Date().toISOString() };
}

export async function chat(settings: Settings, messages: ChatMessage[], snapshot: Snapshot): Promise<string> {
  if (!aiEnabled(settings)) return localChat(messages[messages.length - 1]?.content ?? "", snapshot);
  const out = await post<{ reply: string }>(settings, "/api/chat", { messages, snapshot });
  return out.reply;
}

// ---------- Offline fallbacks ----------

export interface Snapshot {
  currency: string;
  netWorth: number;
  thisMonth: { income: number; spent: number; spendingByCategory: Record<string, number> };
  budgets: { category: string; limit: number; spent: number }[];
  accounts: { name: string; type: string; balance: number }[];
}

function topCategory(s: Snapshot) {
  return Object.entries(s.thisMonth.spendingByCategory).sort((a, b) => b[1] - a[1])[0];
}

function localInsights(s: Snapshot): Insight {
  const m = (n: number) => money(n, s.currency);
  const tips: Insight["tips"] = [];
  const left = s.thisMonth.income - s.thisMonth.spent;
  for (const b of s.budgets) {
    if (b.spent > b.limit) tips.push({ kind: "warning", title: `${b.category} is over budget`, detail: `You've spent ${m(b.spent)} of ${m(b.limit)}.` });
    else if (b.spent > b.limit * 0.8) tips.push({ kind: "info", title: `${b.category} is close to its limit`, detail: `${m(b.limit - b.spent)} left this month.` });
  }
  const top = topCategory(s);
  if (top) tips.push({ kind: "info", title: `Biggest spend: ${top[0]}`, detail: `${m(top[1])} so far this month.` });
  if (s.thisMonth.income > 0 && left > 0) {
    tips.push({ kind: "save", title: "Pay yourself first", detail: `Moving 20% of what's left (${m(left * 0.2)}) into savings builds a safety net.` });
  }
  if (tips.length === 0) tips.push({ kind: "praise", title: "Looking steady", detail: "Add more transactions and budgets to get sharper tips." });
  return {
    headline: left >= 0 ? `You're ${m(left)} ahead this month.` : `You've spent ${m(-left)} more than you earned this month.`,
    tips: tips.slice(0, 4),
    generatedAt: new Date().toISOString(),
  };
}

function localChat(question: string, s: Snapshot): string {
  const m = (n: number) => money(n, s.currency);
  const q = question.toLowerCase();
  if (/net worth|total|how much do i have|balance/.test(q)) {
    return `Your net worth is ${m(s.netWorth)}.\n` + s.accounts.map((a) => `• ${a.name}: ${m(a.balance)}`).join("\n");
  }
  if (/budget/.test(q)) {
    if (!s.budgets.length) return "You haven't set any budgets yet. Add one in the Budgets tab.";
    return s.budgets.map((b) => `• ${b.category}: ${m(b.spent)} of ${m(b.limit)}`).join("\n");
  }
  const top = topCategory(s);
  return (
    `This month you've earned ${m(s.thisMonth.income)} and spent ${m(s.thisMonth.spent)}.` +
    (top ? ` Your biggest category is ${top[0]} (${m(top[1])}).` : "") +
    "\n\nConnect an AI server in Settings for full answers to any question."
  );
}
