import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { z } from "zod";

// Credentials resolve from the environment (ANTHROPIC_API_KEY, or an `ant auth login` profile).
// The key lives only on this server, never in the mobile app bundle.
const client = new Anthropic();

const MODEL = process.env.FINWISE_MODEL ?? "claude-opus-5-5";

// Server-side refusal fallback: if the primary model declines, the API re-runs
// the request on a fallback model chosen by refusal category, inside the same call.
const FALLBACK: { betas: Anthropic.Beta.AnthropicBeta[]; fallbacks: Anthropic.Beta.BetaFallbacksParam } = {
  betas: ["server-side-fallback-2026-07-01"],
  fallbacks: "default",
};

export class AIRefusalError extends Error {}

const SYSTEM_BASE = `You are Finwise, a friendly personal-finance assistant inside a budgeting app used by people of every background and level of financial knowledge.
Write in plain, warm language. Avoid jargon; when a term is unavoidable, explain it in a few words.
Base every number you mention on the data provided. If the data does not answer the question, say so rather than guessing.
You give general educational guidance, not regulated financial, tax or legal advice; for big decisions (investments, debt restructuring, taxes) suggest talking to a qualified professional.`;

// ---------- Categorization ----------

const CategorizeResult = z.object({
  results: z.array(
    z.object({
      id: z.string(),
      category: z.string(),
      merchant: z.string().describe("Clean, human-readable merchant name"),
    }),
  ),
});

export interface CategorizeInput {
  transactions: { id: string; description: string; amount: number }[];
  categories: string[];
}

export async function categorize(input: CategorizeInput) {
  const response = await client.beta.messages.parse({
    model: MODEL,
    max_tokens: 4000,
    ...FALLBACK,
    output_config: { effort: "low", format: betaZodOutputFormat(CategorizeResult) },
    system: SYSTEM_BASE,
    messages: [
      {
        role: "user",
        content: `Assign each transaction exactly one category from this list: ${input.categories.join(", ")}.
Negative amounts are money going out, positive amounts are money coming in (use "Income" for salary, refunds or transfers in when it fits).
Return every id you were given.

Transactions (JSON):
${JSON.stringify(input.transactions)}`,
      },
    ],
  });
  if (response.stop_reason === "refusal") throw new AIRefusalError("The AI declined this request.");
  const parsed = response.parsed_output;
  if (!parsed) throw new Error("Could not parse categorization result");
  const allowed = new Set(input.categories);
  return {
    results: parsed.results.map((r) => ({
      ...r,
      category: allowed.has(r.category) ? r.category : "Other",
    })),
  };
}

// ---------- Natural-language quick add ----------

const ParsedTransaction = z.object({
  description: z.string(),
  amount: z.number().describe("Negative for spending, positive for income"),
  category: z.string(),
  date: z.string().describe("ISO date YYYY-MM-DD"),
  accountName: z.string().nullable().describe("Matching account name if the user mentioned one, else null"),
});

export interface ParseInput {
  text: string;
  today: string;
  categories: string[];
  accounts: string[];
}

export async function parseTransaction(input: ParseInput) {
  const response = await client.beta.messages.parse({
    model: MODEL,
    max_tokens: 2000,
    ...FALLBACK,
    output_config: { effort: "low", format: betaZodOutputFormat(ParsedTransaction) },
    system: SYSTEM_BASE,
    messages: [
      {
        role: "user",
        content: `Turn this note into one transaction. Today is ${input.today}; resolve relative dates like "yesterday" from it.
Category must be one of: ${input.categories.join(", ")}.
Accounts the user has: ${input.accounts.join(", ") || "(none)"}.

Note: """${input.text}"""`,
      },
    ],
  });
  if (response.stop_reason === "refusal") throw new AIRefusalError("The AI declined this request.");
  if (!response.parsed_output) throw new Error("Could not understand that transaction");
  return response.parsed_output;
}

// ---------- Insights ----------

const Insights = z.object({
  headline: z.string().describe("One short sentence summarising this month"),
  tips: z
    .array(
      z.object({
        title: z.string(),
        detail: z.string(),
        kind: z.enum(["save", "warning", "praise", "info"]),
      }),
    )
    .describe("2 to 4 specific, actionable tips grounded in the data"),
});

export async function insights(snapshot: unknown) {
  const response = await client.beta.messages.parse({
    model: MODEL,
    max_tokens: 6000,
    ...FALLBACK,
    output_config: { effort: "medium", format: betaZodOutputFormat(Insights) },
    system: SYSTEM_BASE,
    messages: [
      {
        role: "user",
        content: `Here is a snapshot of my finances. Give me a headline and a few concrete tips.

${JSON.stringify(snapshot)}`,
      },
    ],
  });
  if (response.stop_reason === "refusal") throw new AIRefusalError("The AI declined this request.");
  if (!response.parsed_output) throw new Error("Could not generate insights");
  return response.parsed_output;
}

// ---------- Chat ----------

export interface ChatInput {
  messages: { role: "user" | "assistant"; content: string }[];
  snapshot: unknown;
}

export async function chat(input: ChatInput) {
  const history: Anthropic.Beta.BetaMessageParam[] = input.messages
    .slice(-20)
    .map((m) => ({ role: m.role, content: m.content }));

  const response = await client.beta.messages.create({
    model: MODEL,
    max_tokens: 8000,
    ...FALLBACK,
    output_config: { effort: "medium" },
    system: [
      { type: "text", text: SYSTEM_BASE + "\nKeep answers short enough to read on a phone: a few sentences or a short list." },
      { type: "text", text: `The user's current financial data (JSON):\n${JSON.stringify(input.snapshot)}` },
    ],
    messages: history,
  });
  if (response.stop_reason === "refusal") {
    return { reply: "Sorry, I can't help with that one. Try asking about your spending, budgets or savings." };
  }
  const reply = response.content
    .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === "text")
    .map((b) => b.text)
    .join("\n")
    .trim();
  return { reply: reply || "I couldn't come up with an answer. Could you rephrase?" };
}
