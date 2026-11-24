import React, { useMemo, useState } from "react";
import { Alert, FlatList, Platform, Pressable, Text, TextInput, View } from "react-native";
import { AIError, aiEnabled, categorize } from "../ai";
import { AddTransaction } from "../components/AddTransaction";
import { Button, Chip, Empty, H1, Muted, Sheet } from "../components/ui";
import { CATEGORY_ICONS, money, prettyDate } from "../format";
import { useStore } from "../store";
import { useTheme } from "../theme";
import { CATEGORIES, type Transaction } from "../types";

export function TransactionsScreen() {
  const t = useTheme();
  const { data, updateTransaction, removeTransaction } = useStore();
  const cur = data.settings.currency;
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<string | null>(null);
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const accountName = useMemo(() => Object.fromEntries(data.accounts.map((a) => [a.id, a.name])), [data.accounts]);

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...data.transactions]
      .filter((x) => (!filter || x.category === filter) && (!q || x.description.toLowerCase().includes(q)))
      .sort((a, b) => b.date.localeCompare(a.date));
  }, [data.transactions, query, filter]);

  const uncategorized = data.transactions.filter((x) => x.category === "Other");

  async function autoCategorize() {
    const targets = (uncategorized.length ? uncategorized : data.transactions).slice(0, 100);
    if (!targets.length) return;
    setBusy(true);
    setMessage("");
    try {
      const results = await categorize(
        data.settings,
        targets.map((x) => ({ id: x.id, description: x.description, amount: x.amount })),
        CATEGORIES,
      );
      let changed = 0;
      for (const r of results) {
        const tx = targets.find((x) => x.id === r.id);
        if (tx && tx.category !== r.category) {
          updateTransaction(r.id, { category: r.category, aiCategorized: true });
          changed++;
        }
      }
      setMessage(changed ? `Updated ${changed} transaction${changed > 1 ? "s" : ""}.` : "Everything already looks right.");
    } catch (e) {
      setMessage(e instanceof AIError ? e.message : "Couldn't categorize right now.");
    } finally {
      setBusy(false);
    }
  }

  function remove(tx: Transaction) {
    const go = () => {
      removeTransaction(tx.id);
      setEditing(null);
    };
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(`Delete "${tx.description}"?`)) go();
    } else {
      Alert.alert("Delete transaction?", tx.description, [
        { text: "Cancel", style: "cancel" },
        { text: "Delete", style: "destructive", onPress: go },
      ]);
    }
  }

  return (
    <View style={{ flex: 1 }}>
      <FlatList
        data={list}
        keyExtractor={(x) => x.id}
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
        ListHeaderComponent={
          <View>
            <H1>Transactions</H1>
            <TextInput
              accessibilityLabel="Search transactions"
              placeholder="🔍 Search"
              placeholderTextColor={t.muted}
              value={query}
              onChangeText={setQuery}
              style={{
                minHeight: 44,
                borderRadius: 12,
                paddingHorizontal: 14,
                fontSize: 16,
                backgroundColor: t.card,
                color: t.text,
                borderColor: t.border,
                borderWidth: 1,
                marginBottom: 10,
              }}
            />
            <FlatList
              horizontal
              showsHorizontalScrollIndicator={false}
              data={["All", ...CATEGORIES]}
              keyExtractor={(c) => c}
              renderItem={({ item }) => (
                <Chip
                  label={item}
                  selected={item === "All" ? !filter : filter === item}
                  onPress={() => setFilter(item === "All" ? null : item)}
                />
              )}
            />
            {data.transactions.length > 0 && (
              <View style={{ marginVertical: 8 }}>
                <Button
                  title={
                    uncategorized.length
                      ? `✨ Auto-categorize ${uncategorized.length} uncategorized`
                      : "✨ Re-check categories with AI"
                  }
                  variant="secondary"
                  onPress={autoCategorize}
                  loading={busy}
                />
                {!aiEnabled(data.settings) && <Muted style={{ marginTop: 6 }}>Offline mode uses keyword rules.</Muted>}
                {message ? <Muted style={{ marginTop: 6 }}>{message}</Muted> : null}
              </View>
            )}
          </View>
        }
        ListEmptyComponent={
          <Empty
            icon="🧾"
            title={data.transactions.length ? "No matches" : "No transactions yet"}
            hint={data.transactions.length ? "Try another search or filter." : "Tap + to add your first one."}
          />
        }
        renderItem={({ item }) => (
          <Pressable
            onPress={() => setEditing(item)}
            accessibilityRole="button"
            accessibilityLabel={`${item.description}, ${money(item.amount, cur)}, ${item.category}, ${prettyDate(item.date)}`}
            style={{
              flexDirection: "row",
              alignItems: "center",
              backgroundColor: t.card,
              borderRadius: 14,
              padding: 14,
              marginBottom: 8,
            }}
          >
            <Text style={{ fontSize: 22, marginRight: 12 }}>{CATEGORY_ICONS[item.category] ?? "📦"}</Text>
            <View style={{ flex: 1 }}>
              <Text style={{ color: t.text, fontSize: 16 }} numberOfLines={1}>
                {item.description}
              </Text>
              <Muted>
                {item.category}
                {item.aiCategorized ? " ✨" : ""} · {accountName[item.accountId] ?? "?"} · {prettyDate(item.date)}
              </Muted>
            </View>
            <Text style={{ color: item.amount < 0 ? t.text : t.positive, fontWeight: "600", fontSize: 16 }}>
              {money(item.amount, cur, { sign: true })}
            </Text>
          </Pressable>
        )}
      />

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Add transaction"
        onPress={() => setAdding(true)}
        style={{
          position: "absolute",
          right: 20,
          bottom: 20,
          width: 60,
          height: 60,
          borderRadius: 30,
          backgroundColor: t.primary,
          alignItems: "center",
          justifyContent: "center",
          elevation: 4,
          shadowColor: "#000",
          shadowOpacity: 0.2,
          shadowRadius: 6,
          shadowOffset: { width: 0, height: 3 },
        }}
      >
        <Text style={{ color: t.primaryText, fontSize: 30, lineHeight: 32 }}>+</Text>
      </Pressable>

      <AddTransaction visible={adding} onClose={() => setAdding(false)} />

      <Sheet visible={!!editing} title={editing?.description ?? ""} onClose={() => setEditing(null)}>
        {editing && (
          <>
            <Text style={{ color: t.text, fontSize: 24, fontWeight: "700", marginBottom: 4 }}>
              {money(editing.amount, cur, { sign: true })}
            </Text>
            <Muted style={{ marginBottom: 16 }}>
              {accountName[editing.accountId]} · {prettyDate(editing.date)}
            </Muted>
            <Muted style={{ marginBottom: 6 }}>Change category</Muted>
            <View style={{ flexDirection: "row", flexWrap: "wrap", marginBottom: 16 }}>
              {CATEGORIES.map((c) => (
                <Chip
                  key={c}
                  label={`${CATEGORY_ICONS[c]} ${c}`}
                  selected={editing.category === c}
                  onPress={() => {
                    updateTransaction(editing.id, { category: c, aiCategorized: false });
                    setEditing({ ...editing, category: c });
                  }}
                />
              ))}
            </View>
            <Button title="Delete transaction" variant="danger" onPress={() => remove(editing)} />
          </>
        )}
      </Sheet>
    </View>
  );
}
