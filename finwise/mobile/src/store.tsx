import AsyncStorage from "@react-native-async-storage/async-storage";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { monthKey, todayISO, uid } from "./format";
import type { Account, AppData, Budget, ChatMessage, Insight, Settings, Transaction } from "./types";

const STORAGE_KEY = "finwise:data:v1";

const DEFAULT_SETTINGS: Settings = {
  name: "",
  currency: "USD",
  aiServerUrl: "",
  appToken: "",
};

const EMPTY: AppData = {
  accounts: [],
  transactions: [],
  budgets: [],
  settings: DEFAULT_SETTINGS,
  insight: null,
  chat: [],
};

function daysAgo(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return todayISO(d);
}

export function demoData(settings: Settings): AppData {
  const checking: Account = { id: uid(), name: "Everyday Checking", type: "checking", openingBalance: 2400 };
  const savings: Account = { id: uid(), name: "Rainy-day Savings", type: "savings", openingBalance: 5200 };
  const card: Account = { id: uid(), name: "Credit Card", type: "credit", openingBalance: -320 };
  const t = (accountId: string, amount: number, description: string, category: string, ago: number): Transaction => ({
    id: uid(),
    accountId,
    amount,
    description,
    category,
    date: daysAgo(ago),
  });
  return {
    ...EMPTY,
    settings,
    accounts: [checking, savings, card],
    transactions: [
      t(checking.id, 3200, "Monthly salary", "Income", 1),
      t(checking.id, -1100, "Rent", "Housing", 2),
      t(card.id, -86.4, "Fresh Market groceries", "Groceries", 3),
      t(card.id, -14.99, "Music streaming", "Subscriptions", 4),
      t(card.id, -42.1, "Pizza night", "Dining", 5),
      t(checking.id, -60, "Electricity bill", "Utilities", 6),
      t(card.id, -23.5, "Ride to airport", "Transport", 7),
      t(card.id, -119.0, "Running shoes", "Shopping", 8),
      t(checking.id, -300, "Move to savings", "Transfer", 9),
      t(savings.id, 300, "From checking", "Transfer", 9),
      t(card.id, -54.2, "Weekly groceries", "Groceries", 10),
      t(card.id, -9.5, "Coffee & bagel", "Dining", 11),
      t(card.id, -15.99, "Video streaming", "Subscriptions", 12),
    ],
    budgets: [
      { category: "Groceries", monthlyLimit: 400 },
      { category: "Dining", monthlyLimit: 150 },
      { category: "Shopping", monthlyLimit: 100 },
      { category: "Subscriptions", monthlyLimit: 40 },
    ],
  };
}

interface Store {
  data: AppData;
  loaded: boolean;
  addAccount: (a: Omit<Account, "id">) => void;
  removeAccount: (id: string) => void;
  addTransaction: (t: Omit<Transaction, "id">) => void;
  updateTransaction: (id: string, patch: Partial<Transaction>) => void;
  removeTransaction: (id: string) => void;
  setBudget: (b: Budget) => void;
  removeBudget: (category: string) => void;
  updateSettings: (patch: Partial<Settings>) => void;
  setInsight: (i: Insight | null) => void;
  setChat: (c: ChatMessage[]) => void;
  loadDemo: () => void;
  resetAll: () => void;
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<AppData>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY)
      .then((raw) => {
        if (raw) {
          const parsed = JSON.parse(raw) as Partial<AppData>;
          setData({ ...EMPTY, ...parsed, settings: { ...DEFAULT_SETTINGS, ...parsed.settings } });
        }
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    if (!loaded) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(data)).catch(() => {});
    }, 250);
  }, [data, loaded]);

  const update = useCallback((fn: (d: AppData) => AppData) => setData((d) => fn(d)), []);

  const store = useMemo<Store>(
    () => ({
      data,
      loaded,
      addAccount: (a) => update((d) => ({ ...d, accounts: [...d.accounts, { ...a, id: uid() }] })),
      removeAccount: (id) =>
        update((d) => ({
          ...d,
          accounts: d.accounts.filter((a) => a.id !== id),
          transactions: d.transactions.filter((t) => t.accountId !== id),
        })),
      addTransaction: (t) => update((d) => ({ ...d, transactions: [{ ...t, id: uid() }, ...d.transactions] })),
      updateTransaction: (id, patch) =>
        update((d) => ({ ...d, transactions: d.transactions.map((t) => (t.id === id ? { ...t, ...patch } : t)) })),
      removeTransaction: (id) => update((d) => ({ ...d, transactions: d.transactions.filter((t) => t.id !== id) })),
      setBudget: (b) =>
        update((d) => ({ ...d, budgets: [...d.budgets.filter((x) => x.category !== b.category), b] })),
      removeBudget: (category) => update((d) => ({ ...d, budgets: d.budgets.filter((b) => b.category !== category) })),
      updateSettings: (patch) => update((d) => ({ ...d, settings: { ...d.settings, ...patch } })),
      setInsight: (insight) => update((d) => ({ ...d, insight })),
      setChat: (chat) => update((d) => ({ ...d, chat })),
      loadDemo: () => update((d) => demoData(d.settings)),
      resetAll: () => update((d) => ({ ...EMPTY, settings: d.settings })),
    }),
    [data, loaded, update],
  );

  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

export function useStore() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore must be used inside StoreProvider");
  return s;
}

// ---------- Derived data ----------

export function accountBalance(account: Account, txs: Transaction[]) {
  return txs.filter((t) => t.accountId === account.id).reduce((sum, t) => sum + t.amount, account.openingBalance);
}

export function useSummary() {
  const { data } = useStore();
  return useMemo(() => {
    const month = monthKey(todayISO());
    const balances = data.accounts.map((a) => ({ account: a, balance: accountBalance(a, data.transactions) }));
    const netWorth = balances.reduce((s, b) => s + b.balance, 0);
    const thisMonth = data.transactions.filter((t) => monthKey(t.date) === month && t.category !== "Transfer");
    const income = thisMonth.filter((t) => t.amount > 0).reduce((s, t) => s + t.amount, 0);
    const spent = -thisMonth.filter((t) => t.amount < 0).reduce((s, t) => s + t.amount, 0);
    const byCategory: Record<string, number> = {};
    for (const t of thisMonth) if (t.amount < 0) byCategory[t.category] = (byCategory[t.category] ?? 0) - t.amount;
    return { month, balances, netWorth, income, spent, byCategory };
  }, [data.accounts, data.transactions]);
}

/** Compact, privacy-light view of the user's finances that is sent to the AI. */
export function buildSnapshot(data: AppData, summary: ReturnType<typeof useSummary>) {
  return {
    currency: data.settings.currency,
    today: todayISO(),
    accounts: summary.balances.map((b) => ({ name: b.account.name, type: b.account.type, balance: round(b.balance) })),
    netWorth: round(summary.netWorth),
    thisMonth: {
      income: round(summary.income),
      spent: round(summary.spent),
      spendingByCategory: Object.fromEntries(Object.entries(summary.byCategory).map(([k, v]) => [k, round(v)])),
    },
    budgets: data.budgets.map((b) => ({
      category: b.category,
      limit: b.monthlyLimit,
      spent: round(summary.byCategory[b.category] ?? 0),
    })),
    recentTransactions: data.transactions.slice(0, 60).map((t) => ({
      date: t.date,
      description: t.description,
      amount: round(t.amount),
      category: t.category,
    })),
  };
}

const round = (n: number) => Math.round(n * 100) / 100;
