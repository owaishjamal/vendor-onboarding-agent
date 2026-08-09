import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api";

export function OpsChat({ caseId }: { caseId: string }) {
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");

  const chatMutation = useMutation({
    mutationFn: (msgs: { role: string; content: string }[]) => api.chat(caseId, msgs),
    onSuccess: (data) => {
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
    },
  });

  const handleSend = () => {
    if (!input.trim()) return;
    const newMessages = [...messages, { role: "user", content: input }];
    setMessages(newMessages);
    setInput("");
    chatMutation.mutate(newMessages);
  };

  return (
    <div className="bg-white rounded-xl shadow-sm border border-surface-200 flex flex-col h-full overflow-hidden">
      <div className="p-4 border-b border-surface-200 bg-surface-50 font-medium text-surface-800">
        AI Assistant (Ops)
      </div>
      
      <div className="flex-1 p-4 overflow-y-auto space-y-4" style={{ minHeight: "300px", maxHeight: "500px" }}>
        {messages.length === 0 && (
          <div className="text-surface-500 text-sm text-center italic mt-4">
            Ask me anything about this case's documents or findings.
          </div>
        )}
        
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-lg p-3 text-sm ${
              msg.role === "user" 
                ? "bg-black text-white" 
                : "bg-surface-100 text-surface-900"
            }`}>
              {msg.content}
            </div>
          </div>
        ))}
        {chatMutation.isPending && (
          <div className="flex justify-start">
            <div className="bg-surface-100 text-surface-900 max-w-[85%] rounded-lg p-3 text-sm italic">
              Thinking...
            </div>
          </div>
        )}
      </div>

      <div className="p-3 border-t border-surface-200 bg-surface-50 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder="Ask about the case..."
          className="flex-1 border border-surface-300 rounded px-3 py-2 text-sm focus:border-black focus:ring-1 focus:ring-black outline-none"
          disabled={chatMutation.isPending}
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || chatMutation.isPending}
          className="bg-black text-white px-4 py-2 rounded text-sm font-medium hover:bg-black disabled:opacity-50 transition"
        >
          Send
        </button>
      </div>
    </div>
  );
}
