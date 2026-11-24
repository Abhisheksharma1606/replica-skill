import React, { useState } from "react";
import { Alert, Platform, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, Field, H1, H2, Muted } from "../components/ui";
import { useStore } from "../store";
import { useTheme } from "../theme";

const CURRENCIES = ["USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "NGN", "BRL", "MXN", "ZAR", "PHP"];

export function SettingsScreen() {
  const t = useTheme();
  const { data, updateSettings, loadDemo, resetAll } = useStore();
  const s = data.settings;
  const [status, setStatus] = useState("");
  const [testing, setTesting] = useState(false);

  async function testServer() {
    setTesting(true);
    setStatus("");
    try {
      const res = await fetch(`${s.aiServerUrl.trim().replace(/\/+$/, "")}/health`);
      setStatus(res.ok ? "✅ Connected to the AI server" : `⚠️ Server answered with ${res.status}`);
    } catch {
      setStatus("❌ Couldn't reach that address");
    } finally {
      setTesting(false);
    }
  }

  function confirmReset() {
    const go = () => resetAll();
    const msg = "This deletes all accounts, transactions and budgets on this device.";
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(msg)) go();
    } else {
      Alert.alert("Erase everything?", msg, [
        { text: "Cancel", style: "cancel" },
        { text: "Erase", style: "destructive", onPress: go },
      ]);
    }
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
      <H1>Settings</H1>

      <Card>
        <H2>You</H2>
        <View style={{ height: 12 }} />
        <Field label="Your first name (optional)" value={s.name} onChangeText={(name) => updateSettings({ name })} />
        <Muted style={{ marginBottom: 6 }}>Currency</Muted>
        <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
          {CURRENCIES.map((c) => (
            <Chip key={c} label={c} selected={s.currency === c} onPress={() => updateSettings({ currency: c })} />
          ))}
        </View>
      </Card>

      <Card>
        <H2>✨ AI assistant</H2>
        <Muted style={{ marginVertical: 8 }}>
          Point the app at your Finwise AI server (see the README) to get Claude-powered categorization, insights and
          chat. Without it, the app works fully offline with simpler built-in rules.
        </Muted>
        <Field
          label="AI server address"
          placeholder="http://192.168.1.20:8787"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          value={s.aiServerUrl}
          onChangeText={(aiServerUrl) => updateSettings({ aiServerUrl })}
        />
        <Field
          label="App token (if your server requires one)"
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          value={s.appToken}
          onChangeText={(appToken) => updateSettings({ appToken })}
        />
        <Button title="Test connection" variant="secondary" onPress={testServer} loading={testing} disabled={!s.aiServerUrl.trim()} />
        {status ? <Text style={{ color: t.text, marginTop: 8 }}>{status}</Text> : null}
      </Card>

      <Card>
        <H2>🔒 Privacy</H2>
        <Muted style={{ marginTop: 8 }}>
          Your data is stored only on this device. When AI is on, a summary of your balances, budgets and recent
          transactions is sent to your AI server to answer questions. No bank passwords are ever requested.
        </Muted>
      </Card>

      <Card>
        <H2>Data</H2>
        <View style={{ height: 12 }} />
        <Button title="Load sample data" variant="secondary" onPress={loadDemo} />
        <View style={{ height: 8 }} />
        <Button title="Erase all data" variant="danger" onPress={confirmReset} />
      </Card>
      <Muted style={{ textAlign: "center" }}>Finwise · educational guidance, not financial advice</Muted>
    </ScrollView>
  );
}
