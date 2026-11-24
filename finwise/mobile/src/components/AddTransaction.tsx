import React, { useEffect, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { AIError, aiEnabled, parseTransaction } from "../ai";
import { CATEGORY_ICONS, todayISO } from "../format";
import { guessCategory } from "../localAI";
import { useStore } from "../store";
import { useTheme } from "../theme";
import { CATEGORIES } from "../types";
import { Button, Chip, Field, Muted, Sheet } from "./ui";

export function AddTransaction({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const { data, addTransaction } = useStore();
  const t = useTheme();
  const [note, setNote] = useState("");
  const [parsing, setParsing] = useState(false);
  const [error, setError] = useState("");
  const [isExpense, setIsExpense] = useState(true);
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<string>("");
  const [date, setDate] = useState(todayISO());
  const [accountId, setAccountId] = useState(data.accounts[0]?.id ?? "");

  useEffect(() => {
    if (visible) {
      setNote("");
      setError("");
      setIsExpense(true);
      setAmount("");
      setDescription("");
      setCategory("");
      setDate(todayISO());
      setAccountId((id) => (data.accounts.some((a) => a.id === id) ? id : data.accounts[0]?.id ?? ""));
    }
  }, [visible, data.accounts]);

  async function quickAdd() {
    if (!note.trim()) return;
    setParsing(true);
    setError("");
    try {
      const p = await parseTransaction(
        data.settings,
        note.trim(),
        todayISO(),
        CATEGORIES,
        data.accounts.map((a) => a.name),
      );
      setIsExpense(p.amount <= 0);
      setAmount(String(Math.abs(p.amount)));
      setDescription(p.description);
      setCategory(p.category);
      if (/^\d{4}-\d{2}-\d{2}$/.test(p.date)) setDate(p.date);
      const acct = p.accountName && data.accounts.find((a) => a.name.toLowerCase() === p.accountName!.toLowerCase());
      if (acct) setAccountId(acct.id);
    } catch (e) {
      setError(e instanceof AIError ? e.message : "Couldn't understand that. Try filling the form below.");
    } finally {
      setParsing(false);
    }
  }

  const value = parseFloat(amount.replace(",", "."));
  const valid = !!accountId && !isNaN(value) && value > 0 && description.trim().length > 0 && /^\d{4}-\d{2}-\d{2}$/.test(date);

  function save() {
    if (!valid) return;
    const signed = isExpense ? -value : value;
    addTransaction({
      accountId,
      amount: signed,
      description: description.trim(),
      category: category || guessCategory(description, signed),
      date,
    });
    onClose();
  }

  if (data.accounts.length === 0) {
    return (
      <Sheet visible={visible} title="Add transaction" onClose={onClose}>
        <Muted>Add an account first (Home → Add account), then you can record transactions.</Muted>
      </Sheet>
    );
  }

  return (
    <Sheet visible={visible} title="Add transaction" onClose={onClose}>
      <View style={{ backgroundColor: t.soft, borderRadius: 14, padding: 12, marginBottom: 16 }}>
        <Text style={{ color: t.text, fontWeight: "600", marginBottom: 6 }}>✨ Just type it</Text>
        <Field
          label={aiEnabled(data.settings) ? "Describe it in your own words" : "Describe it (basic offline parsing)"}
          placeholder='e.g. "lunch with Sam 18.50 yesterday on credit card"'
          value={note}
          onChangeText={setNote}
          onSubmitEditing={quickAdd}
          returnKeyType="go"
        />
        <Button title="Fill in for me" variant="secondary" onPress={quickAdd} loading={parsing} disabled={!note.trim()} />
        {error ? <Text style={{ color: t.negative, marginTop: 8 }}>{error}</Text> : null}
      </View>

      <View style={{ flexDirection: "row", marginBottom: 12 }}>
        <Chip label="➖ Money out" selected={isExpense} onPress={() => setIsExpense(true)} />
        <Chip label="➕ Money in" selected={!isExpense} onPress={() => setIsExpense(false)} />
      </View>
      <Field label="Amount" keyboardType="decimal-pad" placeholder="0.00" value={amount} onChangeText={setAmount} />
      <Field label="Description" placeholder="What was it?" value={description} onChangeText={setDescription} />
      <Field label="Date (YYYY-MM-DD)" value={date} onChangeText={setDate} autoCapitalize="none" />

      <Muted style={{ marginBottom: 6 }}>Account</Muted>
      <View style={{ flexDirection: "row", flexWrap: "wrap", marginBottom: 8 }}>
        {data.accounts.map((a) => (
          <Chip key={a.id} label={a.name} selected={a.id === accountId} onPress={() => setAccountId(a.id)} />
        ))}
      </View>

      <Muted style={{ marginBottom: 6 }}>Category {category ? "" : "(leave empty to auto-detect)"}</Muted>
      <View style={{ flexDirection: "row", flexWrap: "wrap", marginBottom: 16 }}>
        {CATEGORIES.map((c) => (
          <Chip
            key={c}
            label={`${CATEGORY_ICONS[c] ?? ""} ${c}`}
            selected={c === category}
            onPress={() => setCategory(c === category ? "" : c)}
          />
        ))}
      </View>
      <Button title="Save transaction" onPress={save} disabled={!valid} />
      <Pressable onPress={onClose} style={{ padding: 16, alignItems: "center" }} accessibilityRole="button">
        <Text style={{ color: t.muted }}>Cancel</Text>
      </Pressable>
    </Sheet>
  );
}
