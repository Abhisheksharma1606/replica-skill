import React, { useState } from "react";
import { Alert, Platform, Pressable, ScrollView, Text, View } from "react-native";
import { AIError, aiEnabled, getInsights } from "../ai";
import { Button, Card, Chip, Empty, Field, H1, H2, Muted, Sheet } from "../components/ui";
import { CATEGORY_ICONS, money } from "../format";
import { buildSnapshot, useStore, useSummary } from "../store";
import { useTheme } from "../theme";
import { ACCOUNT_TYPES, type AccountType } from "../types";

const TIP_ICON = { save: "🐷", warning: "⚠️", praise: "🎉", info: "💡" } as const;

function confirm(message: string, onYes: () => void) {
  if (Platform.OS === "web") {
    if (globalThis.confirm?.(message)) onYes();
    return;
  }
  Alert.alert("Are you sure?", message, [
    { text: "Cancel", style: "cancel" },
    { text: "Delete", style: "destructive", onPress: onYes },
  ]);
}

export function HomeScreen() {
  const t = useTheme();
  const { data, setInsight, addAccount, removeAccount, loadDemo } = useStore();
  const summary = useSummary();
  const cur = data.settings.currency;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("checking");
  const [opening, setOpening] = useState("");

  async function refreshInsights() {
    setLoading(true);
    setError("");
    try {
      setInsight(await getInsights(data.settings, buildSnapshot(data, summary)));
    } catch (e) {
      setError(e instanceof AIError ? e.message : "Couldn't get insights right now.");
    } finally {
      setLoading(false);
    }
  }

  function saveAccount() {
    const bal = parseFloat(opening.replace(",", ".")) || 0;
    if (!name.trim()) return;
    addAccount({ name: name.trim(), type, openingBalance: type === "credit" ? -Math.abs(bal) : bal });
    setName("");
    setOpening("");
    setType("checking");
    setShowAdd(false);
  }

  const greeting = data.settings.name ? `Hi, ${data.settings.name} 👋` : "Hi there 👋";
  const left = summary.income - summary.spent;
  const topCats = Object.entries(summary.byCategory)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4);

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 32 }}>
      <H1>{greeting}</H1>

      <Card style={{ backgroundColor: t.primary, borderColor: t.primary }}>
        <Text style={{ color: t.primaryText, opacity: 0.85 }}>Net worth</Text>
        <Text
          style={{ color: t.primaryText, fontSize: 34, fontWeight: "700", marginVertical: 4 }}
          accessibilityLabel={`Net worth ${money(summary.netWorth, cur)}`}
        >
          {money(summary.netWorth, cur)}
        </Text>
        <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 8 }}>
          <View>
            <Text style={{ color: t.primaryText, opacity: 0.85, fontSize: 13 }}>In this month</Text>
            <Text style={{ color: t.primaryText, fontSize: 17, fontWeight: "600" }}>{money(summary.income, cur)}</Text>
          </View>
          <View>
            <Text style={{ color: t.primaryText, opacity: 0.85, fontSize: 13 }}>Out this month</Text>
            <Text style={{ color: t.primaryText, fontSize: 17, fontWeight: "600" }}>{money(summary.spent, cur)}</Text>
          </View>
          <View>
            <Text style={{ color: t.primaryText, opacity: 0.85, fontSize: 13 }}>Left</Text>
            <Text style={{ color: t.primaryText, fontSize: 17, fontWeight: "600" }}>{money(left, cur)}</Text>
          </View>
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <H2>✨ AI insights</H2>
          <Muted>{aiEnabled(data.settings) ? "Claude" : "Offline"}</Muted>
        </View>
        {data.insight ? (
          <>
            <Text style={{ color: t.text, fontSize: 16, fontWeight: "600", marginBottom: 8 }}>{data.insight.headline}</Text>
            {data.insight.tips.map((tip, i) => (
              <View key={i} style={{ flexDirection: "row", marginBottom: 10 }}>
                <Text style={{ fontSize: 18, marginRight: 10 }}>{TIP_ICON[tip.kind] ?? "💡"}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: t.text, fontWeight: "600" }}>{tip.title}</Text>
                  <Muted>{tip.detail}</Muted>
                </View>
              </View>
            ))}
          </>
        ) : (
          <Muted style={{ marginBottom: 12 }}>Get a plain-language read on your month and tips to save.</Muted>
        )}
        {error ? <Text style={{ color: t.negative, marginBottom: 8 }}>{error}</Text> : null}
        <Button
          title={data.insight ? "Refresh insights" : "Analyse my money"}
          variant="secondary"
          onPress={refreshInsights}
          loading={loading}
          disabled={data.transactions.length === 0}
        />
      </Card>

      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginVertical: 8 }}>
        <H2>Accounts</H2>
        <Pressable accessibilityRole="button" onPress={() => setShowAdd(true)} hitSlop={10}>
          <Text style={{ color: t.primary, fontWeight: "600", fontSize: 16 }}>+ Add account</Text>
        </Pressable>
      </View>

      {summary.balances.length === 0 ? (
        <Card>
          <Empty icon="🏦" title="No accounts yet" hint="Add your bank, card or cash wallet, or try the app with sample data." />
          <Button title="Add my first account" onPress={() => setShowAdd(true)} />
          <View style={{ height: 8 }} />
          <Button title="Try with sample data" variant="secondary" onPress={loadDemo} />
        </Card>
      ) : (
        summary.balances.map(({ account, balance }) => {
          const meta = ACCOUNT_TYPES.find((x) => x.type === account.type);
          return (
            <Pressable
              key={account.id}
              onLongPress={() => confirm(`Delete "${account.name}" and its transactions?`, () => removeAccount(account.id))}
              accessibilityHint="Long press to delete"
            >
              <Card style={{ flexDirection: "row", alignItems: "center" }}>
                <Text style={{ fontSize: 26, marginRight: 12 }}>{meta?.icon}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: t.text, fontSize: 16, fontWeight: "600" }}>{account.name}</Text>
                  <Muted>{meta?.label}</Muted>
                </View>
                <Text style={{ color: balance < 0 ? t.negative : t.text, fontSize: 17, fontWeight: "600" }}>
                  {money(balance, cur)}
                </Text>
              </Card>
            </Pressable>
          );
        })
      )}

      {topCats.length > 0 && (
        <>
          <H2>Where your money went</H2>
          <Card style={{ marginTop: 8 }}>
            {topCats.map(([cat, amt]) => (
              <View key={cat} style={{ flexDirection: "row", justifyContent: "space-between", paddingVertical: 6 }}>
                <Text style={{ color: t.text, fontSize: 15 }}>
                  {CATEGORY_ICONS[cat] ?? "📦"} {cat}
                </Text>
                <Text style={{ color: t.text, fontSize: 15 }}>{money(amt, cur)}</Text>
              </View>
            ))}
          </Card>
        </>
      )}

      <Sheet visible={showAdd} title="Add account" onClose={() => setShowAdd(false)}>
        <Field label="Account name" placeholder="e.g. Main bank" value={name} onChangeText={setName} />
        <Muted style={{ marginBottom: 6 }}>Type</Muted>
        <View style={{ flexDirection: "row", flexWrap: "wrap", marginBottom: 8 }}>
          {ACCOUNT_TYPES.map((a) => (
            <Chip key={a.type} label={`${a.icon} ${a.label}`} selected={type === a.type} onPress={() => setType(a.type)} />
          ))}
        </View>
        <Field
          label={type === "credit" ? "Amount you currently owe" : "Current balance"}
          keyboardType="decimal-pad"
          placeholder="0.00"
          value={opening}
          onChangeText={setOpening}
        />
        <Button title="Save account" onPress={saveAccount} disabled={!name.trim()} />
      </Sheet>
    </ScrollView>
  );
}
