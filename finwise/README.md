# Finwise: AI money manager for everyone

Finwise is a cross-platform mobile app (iOS, Android and web, built with Expo / React Native) for keeping track of
all your money in one place. Its AI features run on Claude.

## Features

| | |
|---|---|
| 🏦 **All your accounts** | Checking, savings, credit cards, cash and investments, with a live net worth |
| 🧾 **Transactions** | Search, filter by category, tap to recategorize or delete |
| ✨ **Type it in your own words** | "lunch with Sam 18.50 yesterday on credit card" becomes a filled-in transaction |
| ✨ **Auto-categorize** | AI sorts transactions into categories in bulk |
| ✨ **AI insights** | A plain-language summary of your month plus 2–4 tips based on your own numbers |
| ✨ **Ask Finwise** | Chat about your finances ("Can I afford a $200 purchase?") |
| 🎯 **Budgets** | Monthly limits per category, with on-track / spending-fast / over-budget status |
| 🌍 **For everyone** | 12 currencies, light/dark mode, screen-reader labels, large touch targets, no jargon |
| 🔒 **Private by default** | Data stays on the device. No bank logins. Works fully offline with built-in rules |

## Layout

```
finwise/
├── mobile/   Expo app (TypeScript)
│   ├── App.tsx              tab shell
│   └── src/
│       ├── screens/         Home, Transactions, Budgets, Assistant, Settings
│       ├── components/      UI kit, Add-transaction sheet
│       ├── store.tsx        state + on-device persistence (AsyncStorage)
│       ├── ai.ts            calls the AI server, falls back to offline logic
│       └── localAI.ts       offline categorizer and note parser
└── server/   Small Node/Express service that talks to Claude
    └── src/ai.ts            categorize, parse, insights, chat
```

The API key stays on the server and is never shipped inside the app.

## Run it

### 1. AI server (optional, but needed for the ✨ features)

```bash
cd finwise/server
npm install
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
npm run dev                 # http://localhost:8787
```

Optional: set `FINWISE_APP_TOKEN` in `.env` and enter the same token in the app's Settings, so only your app
can use your server. `FINWISE_RATE_LIMIT` sets requests per minute per IP (default 30).

The model is `claude-opus-5-5` (override with `FINWISE_MODEL`). Requests turn on the API's server-side refusal
fallback (`fallbacks: "default"`), so a request the main model declines is retried on a fallback model in the same call.

### 2. Mobile app

```bash
cd finwise/mobile
npm install
npx expo start              # scan the QR code with Expo Go, or press w for web
```

In the app, open **Settings → AI assistant** and enter the server address. On a phone, use your computer's
LAN IP (for example `http://192.168.1.20:8787`), not `localhost`. Tap **Test connection**.

No server? The app still works: categorization, quick add, insights and chat fall back to simple on-device rules.

### Checks

```bash
cd finwise/mobile && npm run typecheck
cd finwise/server && npm run typecheck
```

## Disclaimer

Finwise gives general educational guidance, not regulated financial, tax or legal advice.
