import Anthropic from "@anthropic-ai/sdk";
import cors from "cors";
import express, { type NextFunction, type Request, type Response } from "express";
import { z } from "zod";
import { AIRefusalError, categorize, chat, insights, parseTransaction } from "./ai.js";

const app = express();
app.use(cors());
app.use(express.json({ limit: "512kb" }));

// Optional shared secret so only your copy of the app can spend your API credits.
const APP_TOKEN = process.env.FINWISE_APP_TOKEN;
app.use("/api", (req, res, next) => {
  if (APP_TOKEN && req.header("x-finwise-token") !== APP_TOKEN) {
    res.status(401).json({ error: "Invalid app token" });
    return;
  }
  next();
});

// Tiny in-memory rate limiter (per IP, per minute).
const hits = new Map<string, { count: number; reset: number }>();
const LIMIT = Number(process.env.FINWISE_RATE_LIMIT ?? 30);
app.use("/api", (req, res, next) => {
  const key = req.ip ?? "unknown";
  const now = Date.now();
  const entry = hits.get(key);
  if (!entry || entry.reset < now) {
    hits.set(key, { count: 1, reset: now + 60_000 });
  } else if (++entry.count > LIMIT) {
    res.status(429).json({ error: "Too many requests, slow down a little." });
    return;
  }
  next();
});

const Categorize = z.object({
  transactions: z
    .array(z.object({ id: z.string(), description: z.string().max(300), amount: z.number() }))
    .min(1)
    .max(100),
  categories: z.array(z.string().max(40)).min(1).max(40),
});

const Parse = z.object({
  text: z.string().min(1).max(500),
  today: z.string(),
  categories: z.array(z.string().max(40)).min(1).max(40),
  accounts: z.array(z.string().max(60)).max(30),
});

const Chat = z.object({
  messages: z
    .array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().max(4000) }))
    .min(1),
  snapshot: z.unknown(),
});

const Insights = z.object({ snapshot: z.unknown() });

type Handler = (req: Request, res: Response) => Promise<void>;
const route = (fn: Handler) => (req: Request, res: Response, next: NextFunction) =>
  fn(req, res).catch(next);

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.post(
  "/api/categorize",
  route(async (req, res) => {
    res.json(await categorize(Categorize.parse(req.body)));
  }),
);

app.post(
  "/api/parse",
  route(async (req, res) => {
    res.json(await parseTransaction(Parse.parse(req.body)));
  }),
);

app.post(
  "/api/insights",
  route(async (req, res) => {
    res.json(await insights(Insights.parse(req.body).snapshot));
  }),
);

app.post(
  "/api/chat",
  route(async (req, res) => {
    const body = Chat.parse(req.body);
    res.json(await chat({ messages: body.messages, snapshot: body.snapshot }));
  }),
);

app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (err instanceof z.ZodError) {
    res.status(400).json({ error: "Invalid request", details: err.issues });
  } else if (err instanceof AIRefusalError) {
    res.status(422).json({ error: err.message });
  } else if (err instanceof Anthropic.AuthenticationError) {
    console.error("Anthropic auth failed - check ANTHROPIC_API_KEY");
    res.status(502).json({ error: "AI service is not configured on the server." });
  } else if (err instanceof Anthropic.RateLimitError) {
    res.status(503).json({ error: "AI is busy right now, please try again shortly." });
  } else if (err instanceof Anthropic.APIError) {
    console.error(`Anthropic API error ${err.status}:`, err.message);
    res.status(502).json({ error: "AI service error, please try again." });
  } else if (err instanceof Anthropic.AnthropicError) {
    // e.g. no credentials found at all
    console.error("Anthropic client error:", err.message);
    res.status(502).json({ error: "AI service is not configured on the server." });
  } else {
    console.error(err);
    res.status(500).json({ error: "Something went wrong." });
  }
});

if (!process.env.ANTHROPIC_API_KEY && !process.env.ANTHROPIC_AUTH_TOKEN) {
  console.warn("Warning: ANTHROPIC_API_KEY is not set. AI requests will fail unless an `ant auth login` profile exists.");
}

const port = Number(process.env.PORT ?? 8787);
app.listen(port, () => console.log(`Finwise AI server listening on http://localhost:${port}`));
