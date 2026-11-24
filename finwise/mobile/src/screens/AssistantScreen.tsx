import React, { useRef, useState } from "react";
import { ActivityIndicator, FlatList, KeyboardAvoidingView, Platform, Pressable, Text, TextInput, View } from "react-native";
import { AIError, aiEnabled, chat } from "../ai";
import { Chip, H1, Muted } from "../components/ui";
import { buildSnapshot, useStore, useSummary } from "../store";
import { useTheme } from "../theme";
import type { ChatMessage } from "../types";

const SUGGESTIONS = [
  "How am I doing this month?",
  "Where can I cut back?",
  "Can I afford a $200 purchase?",
  "Help me build an emergency fund",
  "Explain how credit card interest works",
];

export function AssistantScreen() {
  const t = useTheme();
  const { data, setChat } = useStore();
  const summary = useSummary();
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const listRef = useRef<FlatList<ChatMessage>>(null);

  async function send(text: string) {
    const content = text.trim();
    if (!content || thinking) return;
    const next: ChatMessage[] = [...data.chat, { role: "user", content }];
    setChat(next);
    setInput("");
    setThinking(true);
    try {
      const reply = await chat(data.settings, next, buildSnapshot(data, summary));
      setChat([...next, { role: "assistant", content: reply }]);
    } catch (e) {
      const msg = e instanceof AIError ? e.message : "Something went wrong.";
      setChat([...next, { role: "assistant", content: `⚠️ ${msg}` }]);
    } finally {
      setThinking(false);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={90}>
      <View style={{ paddingHorizontal: 16, paddingTop: 16, flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" }}>
        <H1>Ask Finwise</H1>
        {data.chat.length > 0 && (
          <Pressable onPress={() => setChat([])} accessibilityRole="button" hitSlop={10}>
            <Text style={{ color: t.primary }}>Clear</Text>
          </Pressable>
        )}
      </View>
      <FlatList
        ref={listRef}
        data={data.chat}
        keyExtractor={(_, i) => String(i)}
        contentContainerStyle={{ padding: 16, flexGrow: 1 }}
        onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
        ListEmptyComponent={
          <View>
            <Muted style={{ marginBottom: 12 }}>
              Ask anything about your money in your own words. {aiEnabled(data.settings) ? "" : "(Offline mode gives basic answers — add an AI server in Settings for full answers.)"}
            </Muted>
            <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
              {SUGGESTIONS.map((s) => (
                <Chip key={s} label={s} onPress={() => send(s)} />
              ))}
            </View>
          </View>
        }
        renderItem={({ item }) => {
          const mine = item.role === "user";
          return (
            <View
              style={{
                alignSelf: mine ? "flex-end" : "flex-start",
                maxWidth: "85%",
                backgroundColor: mine ? t.primary : t.card,
                borderRadius: 16,
                padding: 12,
                marginBottom: 8,
              }}
            >
              <Text selectable style={{ color: mine ? t.primaryText : t.text, fontSize: 16, lineHeight: 22 }}>
                {item.content}
              </Text>
            </View>
          );
        }}
        ListFooterComponent={
          thinking ? (
            <View style={{ flexDirection: "row", alignItems: "center", padding: 8 }}>
              <ActivityIndicator color={t.primary} />
              <Muted style={{ marginLeft: 8 }}>Thinking…</Muted>
            </View>
          ) : undefined
        }
      />
      <View style={{ flexDirection: "row", padding: 12, borderTopWidth: 1, borderColor: t.border, backgroundColor: t.card }}>
        <TextInput
          accessibilityLabel="Message"
          placeholder="Ask about your finances…"
          placeholderTextColor={t.muted}
          value={input}
          onChangeText={setInput}
          onSubmitEditing={() => send(input)}
          returnKeyType="send"
          multiline
          style={{
            flex: 1,
            minHeight: 44,
            maxHeight: 120,
            borderRadius: 22,
            paddingHorizontal: 16,
            paddingVertical: 10,
            fontSize: 16,
            color: t.text,
            backgroundColor: t.bg,
          }}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Send"
          onPress={() => send(input)}
          disabled={!input.trim() || thinking}
          style={{
            marginLeft: 8,
            width: 44,
            height: 44,
            borderRadius: 22,
            backgroundColor: t.primary,
            opacity: !input.trim() || thinking ? 0.5 : 1,
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Text style={{ color: t.primaryText, fontSize: 20 }}>↑</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}
