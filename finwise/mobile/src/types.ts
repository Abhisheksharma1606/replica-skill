export type AccountType = "checking" | "savings" | "credit" | "cash" | "investment";

export interface Account {
  id: string;
  name: string;
  type: AccountType;
  /** Balance when the account was added; transactions are applied on top. */
  openingBalance: number;
}

export interface Transaction {
  id: string;
  accountId: string;
  /** Negative = money out, positive = money in. */
  amount: number;
  description: string;
  category: string;
  /** ISO date, YYYY-MM-DD */
  date: string;
  aiCategorized?: boolean;
}

export interface Budget {
  category: string;
  monthlyLimit: number;
}

export interface Settings {
  name: string;
  currency: string;
  aiServerUrl: string;
  appToken: string;
}

export interface Insight {
  headline: string;
  tips: { title: string; detail: string; kind: "save" | "warning" | "praise" | "info" }[];
  generatedAt: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AppData {
  accounts: Account[];
  transactions: Transaction[];
  budgets: Budget[];
  settings: Settings;
  insight: Insight | null;
  chat: ChatMessage[];
}

export const CATEGORIES = [
  "Groceries",
  "Dining",
  "Transport",
  "Housing",
  "Utilities",
  "Shopping",
  "Health",
  "Entertainment",
  "Travel",
  "Education",
  "Subscriptions",
  "Income",
  "Transfer",
  "Other",
] as const;

export const ACCOUNT_TYPES: { type: AccountType; label: string; icon: string }[] = [
  { type: "checking", label: "Checking", icon: "🏦" },
  { type: "savings", label: "Savings", icon: "🐷" },
  { type: "credit", label: "Credit card", icon: "💳" },
  { type: "cash", label: "Cash", icon: "💵" },
  { type: "investment", label: "Investment", icon: "📈" },
];
