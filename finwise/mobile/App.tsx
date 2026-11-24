import { StatusBar } from "expo-status-bar";
import React, { useState } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { AssistantScreen } from "./src/screens/AssistantScreen";
import { BudgetsScreen } from "./src/screens/BudgetsScreen";
import { HomeScreen } from "./src/screens/HomeScreen";
import { SettingsScreen } from "./src/screens/SettingsScreen";
import { TransactionsScreen } from "./src/screens/TransactionsScreen";
import { StoreProvider, useStore } from "./src/store";
import { useTheme } from "./src/theme";

const TABS = [
  { key: "home", label: "Home", icon: "🏠", Screen: HomeScreen },
  { key: "tx", label: "Activity", icon: "🧾", Screen: TransactionsScreen },
  { key: "budgets", label: "Budgets", icon: "🎯", Screen: BudgetsScreen },
  { key: "ask", label: "Ask AI", icon: "✨", Screen: AssistantScreen },
  { key: "settings", label: "Settings", icon: "⚙️", Screen: SettingsScreen },
] as const;

function Shell() {
  const t = useTheme();
  const { loaded } = useStore();
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("home");
  const Active = TABS.find((x) => x.key === tab)!.Screen;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: t.bg }} edges={["top", "left", "right"]}>
      <StatusBar style="auto" />
      <View style={{ flex: 1 }}>
        {loaded ? (
          <Active />
        ) : (
          <ActivityIndicator style={{ marginTop: 80 }} color={t.primary} />
        )}
      </View>
      <SafeAreaView edges={["bottom"]} style={{ backgroundColor: t.card }}>
        <View accessibilityRole="tablist" style={{ flexDirection: "row", borderTopWidth: 1, borderColor: t.border }}>
          {TABS.map((x) => {
            const selected = x.key === tab;
            return (
              <Pressable
                key={x.key}
                accessibilityRole="tab"
                accessibilityState={{ selected }}
                accessibilityLabel={x.label}
                onPress={() => setTab(x.key)}
                style={{ flex: 1, alignItems: "center", paddingVertical: 8, minHeight: 56 }}
              >
                <Text style={{ fontSize: 22, opacity: selected ? 1 : 0.6 }}>{x.icon}</Text>
                <Text style={{ fontSize: 12, color: selected ? t.primary : t.muted, fontWeight: selected ? "700" : "400" }}>
                  {x.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </SafeAreaView>
    </SafeAreaView>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <StoreProvider>
        <Shell />
      </StoreProvider>
    </SafeAreaProvider>
  );
}
