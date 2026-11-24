import React, { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, Empty, Field, H1, Muted, Progress, Sheet } from "../components/ui";
import { CATEGORY_ICONS, money } from "../format";
import { useStore, useSummary } from "../store";
import { useTheme } from "../theme";
import { CATEGORIES } from "../types";

export function BudgetsScreen() {
  const t = useTheme();
  const { data, setBudget, removeBudget } = useStore();
  const summary = useSummary();
  const cur = data.settings.currency;
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<string>("Groceries");
  const [limit, setLimit] = useState("");

  const day = new Date().getDate();
  const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
  const monthProgress = day / daysInMonth;

  function openEditor(cat?: string) {
    const existing = data.budgets.find((b) => b.category === cat);
    setCategory(cat ?? CATEGORIES.find((c) => !data.budgets.some((b) => b.category === c) && c !== "Income") ?? "Other");
    setLimit(existing ? String(existing.monthlyLimit) : "");
    setOpen(true);
  }

  function save() {
    const v = parseFloat(limit.replace(",", "."));
    if (!v || v <= 0) return;
    setBudget({ category, monthlyLimit: v });
    setOpen(false);
  }

  const totalLimit = data.budgets.reduce((s, b) => s + b.monthlyLimit, 0);
  const totalSpent = data.budgets.reduce((s, b) => s + (summary.byCategory[b.category] ?? 0), 0);

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 32 }}>
      <H1>Budgets</H1>
      {data.budgets.length > 0 && (
        <Card>
          <Muted>This month across all budgets</Muted>
          <Text style={{ color: t.text, fontSize: 22, fontWeight: "700", marginVertical: 6 }}>
            {money(totalSpent, cur)} <Text style={{ color: t.muted, fontSize: 16 }}>of {money(totalLimit, cur)}</Text>
          </Text>
          <Progress value={totalLimit ? totalSpent / totalLimit : 0} color={totalSpent > totalLimit ? t.negative : t.primary} />
          <Muted style={{ marginTop: 6 }}>
            {Math.round(monthProgress * 100)}% of the month has passed.
          </Muted>
        </Card>
      )}

      {data.budgets.length === 0 ? (
        <Card>
          <Empty icon="🎯" title="No budgets yet" hint="Set a monthly limit for a category and we'll track it for you." />
        </Card>
      ) : (
        [...data.budgets]
          .sort((a, b) => a.category.localeCompare(b.category))
          .map((b) => {
            const spent = summary.byCategory[b.category] ?? 0;
            const ratio = spent / b.monthlyLimit;
            const color = ratio > 1 ? t.negative : ratio > monthProgress + 0.1 ? t.warning : t.positive;
            const status = ratio > 1 ? "Over budget" : ratio > monthProgress + 0.1 ? "Spending fast" : "On track";
            return (
              <Pressable key={b.category} onPress={() => openEditor(b.category)} accessibilityRole="button">
                <Card>
                  <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 8 }}>
                    <Text style={{ color: t.text, fontSize: 16, fontWeight: "600" }}>
                      {CATEGORY_ICONS[b.category]} {b.category}
                    </Text>
                    <Text style={{ color, fontWeight: "600" }}>{status}</Text>
                  </View>
                  <Progress value={ratio} color={color} />
                  <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 6 }}>
                    <Muted>
                      {money(spent, cur)} of {money(b.monthlyLimit, cur)}
                    </Muted>
                    <Muted>
                      {ratio > 1 ? `${money(spent - b.monthlyLimit, cur)} over` : `${money(b.monthlyLimit - spent, cur)} left`}
                    </Muted>
                  </View>
                </Card>
              </Pressable>
            );
          })
      )}
      <Button title="+ New budget" onPress={() => openEditor()} />

      <Sheet visible={open} title="Budget" onClose={() => setOpen(false)}>
        <Muted style={{ marginBottom: 6 }}>Category</Muted>
        <View style={{ flexDirection: "row", flexWrap: "wrap", marginBottom: 8 }}>
          {CATEGORIES.filter((c) => c !== "Income" && c !== "Transfer").map((c) => (
            <Chip key={c} label={`${CATEGORY_ICONS[c]} ${c}`} selected={category === c} onPress={() => setCategory(c)} />
          ))}
        </View>
        <Field label="Monthly limit" keyboardType="decimal-pad" placeholder="0.00" value={limit} onChangeText={setLimit} />
        <Button title="Save budget" onPress={save} />
        {data.budgets.some((b) => b.category === category) && (
          <Button
            title="Remove budget"
            variant="danger"
            style={{ marginTop: 8 }}
            onPress={() => {
              removeBudget(category);
              setOpen(false);
            }}
          />
        )}
      </Sheet>
    </ScrollView>
  );
}
