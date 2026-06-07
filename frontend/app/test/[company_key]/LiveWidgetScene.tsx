"use client";

import { useEffect, useRef, useState, FormEvent } from "react";
import { Orb } from "@/components/ui/orb";
import { TiltedCard } from "@/app/TiltedCard";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useRoomContext,
  useVoiceAssistant,
  useLocalParticipant,
} from "@livekit/components-react";

type Msg = { sender: "user" | "ai"; text: string };

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export function LiveWidgetScene({ companyKey, productId }: { companyKey: string, productId: string }) {
  const [mode, setMode] = useState<"chat" | "voice">("chat");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [typing, setTyping] = useState("");
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  // LiveKit Connection State
  const [lkToken, setLkToken] = useState("");
  const [lkUrl, setLkUrl] = useState("");

  const chatContainerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll chat
  useEffect(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [messages, typing, isLoading]);

  // Handle switching to voice mode -> fetch token
  useEffect(() => {
    if (mode === "voice" && !lkToken) {
      const fetchToken = async () => {
        try {
          const res = await fetch(API_BASE.replace('/api', '/connection-details'), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ company_key: companyKey, modality: "voice" })
          });
          if (res.ok) {
            const data = await res.json();
            setLkToken(data.token);
            setLkUrl(data.url || data.serverUrl);
          } else {
            console.error("Failed to fetch LiveKit token");
          }
        } catch (e) {
          console.error("LiveKit connection error", e);
        }
      };
      fetchToken();
    }
  }, [mode, companyKey, lkToken]);

  const handleChatSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMsg = input;
    setMessages(prev => [...prev, { sender: "user", text: userMsg }]);
    setInput("");
    setIsLoading(true);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company_key: companyKey, query: userMsg, product_id: productId })
      });
      if (res.ok) {
        const data = await res.json();
        setMessages(prev => [...prev, { sender: "ai", text: data.answer }]);
      } else {
        setMessages(prev => [...prev, { sender: "ai", text: "Sorry, I encountered an error answering your question." }]);
      }
    } catch (e) {
      console.error(e);
      setMessages(prev => [...prev, { sender: "ai", text: "Network error occurred." }]);
    } finally {
      setIsLoading(false);
    }
  };

  const showVideo = true; 
  const showLine = true;
  const showDocs = true;
  const showChart = messages.length >= 2;
  const showMemory = messages.length >= 3;

  return (
    <div className="fade-rise relative mt-10 flex w-full max-w-[320px] flex-1 flex-col items-center">
      
      {/* Side video panel - mocked for demonstration of "See" feature */}
      <div
        className={`absolute right-full transition-all duration-700 ease-out translate-x-0 opacity-100`}
        style={{ top: 0, marginRight: 160 }}
      >
        <div
          className="overflow-hidden rounded-2xl bg-white transition-transform duration-300 ease-out hover:scale-[1.04]"
          style={{
            width: 300,
            height: 180,
            boxShadow: "0 14px 36px rgba(50,72,93,0.22)",
          }}
        >
          <video
            src="/test-vid.mov"
            autoPlay
            muted
            loop
            playsInline
            className="h-full w-full object-cover"
          />
        </div>
        <p
          className="mt-3 text-center text-lg font-semibold text-white"
          style={{ letterSpacing: "-0.035em" }}
        >
          Live customer view
        </p>
      </div>

      {/* Mode toggle */}
      <div
        className="mb-4 flex rounded-full p-1 backdrop-blur"
        style={{ backgroundColor: "rgba(255,255,255,0.18)", zIndex: 10 }}
      >
        {(["chat", "voice"] as const).map((m: "chat" | "voice") => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className="rounded-full px-5 py-1.5 text-sm font-semibold transition-colors"
            style={{
              backgroundColor: mode === m ? "white" : "transparent",
              color: mode === m ? "#5E8EBE" : "rgba(255,255,255,0.85)",
              letterSpacing: "-0.02em",
            }}
          >
            {m === "chat" ? "Chat" : "Voice"}
          </button>
        ))}
      </div>

      <TiltedCard
        className="flex h-[520px] w-full"
        innerClassName="flex w-full flex-1 flex-col rounded-[20px] bg-white"
      >
        <div
          className="flex items-center justify-between px-6 pt-6"
          style={{ color: "#B1C8E0", transform: "translateZ(20px)" }}
        >
          <div className="flex items-center gap-2">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              width="22"
              height="22"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M15 18C15 18 9.00001 13.5811 9 12C8.99999 10.4188 15 6 15 6" />
            </svg>
            <span className="text-base font-semibold" style={{ letterSpacing: "-0.02em" }}>
              Clutch Live
            </span>
          </div>
        </div>
        <div className="mx-6 mt-5 h-px rounded-full" style={{ backgroundColor: "#F9FAFA" }} />

        {mode === "voice" ? (
          lkToken && lkUrl ? (
            <LiveKitRoom
              token={lkToken}
              serverUrl={lkUrl}
              connect={true}
              audio={true}
              video={true} // Enable video to test See mode!
            >
              <ActiveVoiceMode />
              <RoomAudioRenderer />
            </LiveKitRoom>
          ) : (
            <div className="flex flex-1 items-center justify-center text-[#5E8EBE] font-semibold text-sm">
              Connecting to LiveKit...
            </div>
          )
        ) : (
          <>
            {/* Messages */}
            <div
              ref={chatContainerRef}
              className="flex flex-col gap-2.5 px-6 pt-3 font-medium overflow-y-auto flex-1"
              style={{
                letterSpacing: "-0.025em",
                transform: "translateZ(55px)",
                transformStyle: "preserve-3d",
              }}
            >
              {messages.length === 0 && !isLoading && (
                <div className="text-center text-sm text-gray-400 mt-10">
                  Try asking a question about the device.
                </div>
              )}
              {messages.map((m, i) =>
                m.sender === "user" ? (
                  <div
                    key={i}
                    className="msg-pop w-fit max-w-[85%] self-end rounded-2xl px-3 py-2 text-left text-[0.8125rem] leading-[1.15] text-white [text-wrap:pretty]"
                    style={{ backgroundColor: "#6291C0" }}
                  >
                    {m.text}
                  </div>
                ) : (
                  <div
                    key={i}
                    className="msg-pop w-fit max-w-[85%] self-start rounded-2xl rounded-bl-none px-3 py-2 text-left text-[0.8125rem] leading-[1.15] [text-wrap:pretty]"
                    style={{ backgroundColor: "#E6E8EB", color: "#32485D" }}
                  >
                    {m.text}
                  </div>
                )
              )}
              {isLoading && (
                <div
                  className="msg-pop w-fit max-w-[65%] self-start rounded-2xl rounded-bl-none px-3 py-2 text-left text-[0.8125rem] leading-[1.15] [text-wrap:pretty] animate-pulse"
                  style={{ backgroundColor: "#E6E8EB", color: "#32485D" }}
                >
                  ...
                </div>
              )}
            </div>

            {/* Input bar */}
            <form onSubmit={handleChatSubmit}
              className="mx-5 mt-auto mb-5 flex flex-col gap-2 rounded-xl px-3.5 pt-3 pb-3"
              style={{
                border: "1.5px solid #E6E8EB",
                transform: "translateZ(35px)",
                transformStyle: "preserve-3d",
              }}
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask me something.."
                className="min-h-[1.4em] text-left text-[0.8125rem] [text-wrap:pretty] outline-none"
                style={{ color: "#32485D", letterSpacing: "-0.02em" }}
                disabled={isLoading}
              />
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3" style={{ color: "#B1C8E0" }}>
                </div>
                <div className="flex items-center gap-3">
                  <button
                    type="submit"
                    disabled={!input.trim() || isLoading}
                    className="send-btn flex h-9 w-9 items-center justify-center rounded-full text-white disabled:opacity-50"
                    style={{ backgroundColor: "#6291C0" }}
                    aria-label="Send"
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      width="18"
                      height="18"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      style={{ transform: "rotate(180deg)" }}
                    >
                      <path d="M18 9.00005C18 9.00005 13.5811 15 12 15C10.4188 15 6 9 6 9" />
                    </svg>
                  </button>
                </div>
              </div>
            </form>
          </>
        )}
      </TiltedCard>
    </div>
  );
}

function ActiveVoiceMode() {
  const { state, audioTrack } = useVoiceAssistant();
  const { localParticipant } = useLocalParticipant();
  
  // This is the FIX for the video/camera issue we diagnosed!
  // Force enable the camera track so the worker receives the video feed
  useEffect(() => {
    if (localParticipant) {
      localParticipant.setCameraEnabled(true).catch(e => console.error("Failed to enable camera:", e));
      localParticipant.setMicrophoneEnabled(true).catch(e => console.error("Failed to enable mic:", e));
    }
  }, [localParticipant]);

  let agentState: "listening" | "talking" | "thinking" = "listening";
  if ((state as string) === "speaking") agentState = "talking";
  else if ((state as string) === "thinking" || (state as string) === "responding") agentState = "thinking";

  return (
    <div
      className="flex flex-1 flex-col items-center justify-center px-6 pb-8"
      style={{ transform: "translateZ(55px)", transformStyle: "preserve-3d" }}
    >
      <div className="orb-float" style={{ width: 160, height: 160 }}>
        <Orb
          colors={["#CCE0EB", "#91BDDB"]}
          agentState={agentState}
          volumeMode="manual"
          manualInput={agentState === "listening" ? 0.55 : 0.1}
          manualOutput={agentState === "talking" ? 0.7 : 0.05}
        />
      </div>
      <div className="mt-7 min-h-[3em] w-full max-w-[240px] px-2 text-center">
        <p
          className="text-[0.75rem] font-semibold leading-[1.25] [text-wrap:pretty]"
          style={{ color: "#32485D", letterSpacing: "-0.02em" }}
        >
          {state === "speaking" ? "Clutch is speaking..." : state === "listening" ? "Listening..." : "Thinking..."}
        </p>
      </div>
    </div>
  );
}
