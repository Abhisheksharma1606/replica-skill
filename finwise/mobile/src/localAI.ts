// Offline fallbacks so the app stays useful with no AI server configured.
import { todayISO } from "./format";

const RULES: [RegExp, string][] = [
  [/salary|payroll|paycheck|deposit|refund|interest|dividend|freelance|invoice/i, "Income"],
  [/transfer|venmo|zelle|paypal|wire/i, "Transfer"],
  [/grocery|groceries|supermarket|market|aldi|lidl|kroger|safeway|trader|whole foods|costco|walmart/i, "Groceries"],
  [/restaurant|cafe|coffee|starbucks|pizza|burger|lunch|dinner|breakfast|bar|takeout|doordash|uber eats|deliveroo|swiggy|zomato/i, "Dining"],
  [/uber|lyft|taxi|bus|train|metro|fuel|gas station|petrol|parking|toll|shell|chevron/i, "Transport"],
  [/rent|mortgage|landlord|hoa/i, "Housing"],
  [/electric|water|internet|phone|mobile|utility|power|broadband/i, "Utilities"],
  [/netflix|spotify|prime|hulu|disney|subscription|icloud|youtube|membership/i, "Subscriptions"],
  [/amazon|store|mall|clothes|shoes|ikea|target|flipkart/i, "Shopping"],
  [/pharmacy|doctor|hospital|clinic|dental|gym|medicine|cvs|walgreens/i, "Health"],
  [/movie|cinema|concert|game|steam|ticket/i, "Entertainment"],
  [/hotel|airbnb|flight|airline|booking/i, "Travel"],
  [/school|tuition|course|udemy|book/i, "Education"],
];

export function guessCategory(description: string, amount: number): string {
  for (const [re, cat] of RULES) if (re.test(description)) return cat;
  return amount > 0 ? "Income" : "Other";
}

/** Very small parser for notes like "coffee 4.50 yesterday" or "got paid 2000". */
export function parseLocally(text: string) {
  const num = text.match(/(\d+(?:[.,]\d{1,2})?)/);
  const value = num ? parseFloat(num[1].replace(",", ".")) : 0;
  const isIncome = /paid|salary|received|got|income|refund|earned/i.test(text);
  const d = new Date();
  if (/yesterday/i.test(text)) d.setDate(d.getDate() - 1);
  const description =
    text
      .replace(num?.[0] ?? "", "")
      .replace(/\b(yesterday|today|on|for|at|spent|paid|\$|€|£|₹)\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim() || "Transaction";
  const amount = isIncome ? value : -value;
  return {
    description: description.charAt(0).toUpperCase() + description.slice(1),
    amount,
    category: guessCategory(text, amount),
    date: todayISO(d),
    accountName: null as string | null,
  };
}
