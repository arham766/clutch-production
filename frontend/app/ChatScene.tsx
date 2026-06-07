"use client";

import { useEffect, useRef, useState } from "react";
import { Orb } from "@/components/ui/orb";
import { TiltedCard } from "./TiltedCard";

type Msg = { sender: "user" | "ai"; text: string };

const SCRIPT: Msg[] = [
  { sender: "user", text: "My printer isn't feeding paper from the bottom tray." },
  {
    sender: "ai",
    text: "Got it. Can you share the model number and confirm the tray latches all the way shut?",
  },
  { sender: "user", text: "HP LaserJet Pro M404n. Latch clicks but paper still won't pull." },
  {
    sender: "ai",
    text: "Likely the separation pad is worn. I'll walk you through replacing it. Takes about 5 minutes.",
  },
];

const START_DELAY = 2900;
const TYPE_MS = 32;
const AFTER_TYPE_PAUSE = 450;
const POST_SEND_GAP = 600;
const AI_THINK = 850;
const AI_PAUSE = 600;

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const id = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(id);
      reject(new Error("aborted"));
    });
  });
}

export function ChatScene() {
  const [mode, setMode] = useState<"chat" | "voice">("chat");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [typing, setTyping] = useState("");
  const [pulse, setPulse] = useState(0);
  const [streamedAI, setStreamedAI] = useState("");
  const [streamedUser, setStreamedUser] = useState("");

  const isFirstRunRef = useRef(true);

  useEffect(() => {
    setMessages([]);
    setTyping("");
    setPulse(0);
    setStreamedAI("");
    setStreamedUser("");
    const ctrl = new AbortController();
    const { signal } = ctrl;
    const initialDelay = isFirstRunRef.current ? START_DELAY : 600;
    isFirstRunRef.current = false;
    (async () => {
      try {
        await wait(initialDelay, signal);
        for (let i = 0; i < SCRIPT.length; i++) {
          const msg = SCRIPT[i];
          if (msg.sender === "user") {
            if (mode === "voice") {
              setStreamedAI("");
              setStreamedUser("");
              await wait(500, signal);
              for (let k = 1; k <= msg.text.length; k++) {
                setStreamedUser(msg.text.slice(0, k));
                await wait(34, signal);
              }
              setMessages((m) => [...m, msg]);
              await wait(POST_SEND_GAP, signal);
            } else {
              for (let k = 1; k <= msg.text.length; k++) {
                setTyping(msg.text.slice(0, k));
                await wait(TYPE_MS, signal);
              }
              await wait(AFTER_TYPE_PAUSE, signal);
              setPulse((p) => p + 1);
              await wait(160, signal);
              setTyping("");
              setMessages((m) => [...m, msg]);
              await wait(POST_SEND_GAP, signal);
            }
          } else {
            const think =
              mode === "voice" ? 1100 : i === 1 ? 1900 : AI_THINK;
            await wait(think, signal);
            if (mode === "voice") {
              for (let k = 1; k <= msg.text.length; k++) {
                setStreamedAI(msg.text.slice(0, k));
                await wait(28, signal);
              }
              setMessages((m) => [...m, msg]);
            } else {
              setMessages((m) => [...m, msg]);
            }
            await wait(AI_PAUSE, signal);
          }
        }
      } catch {
        // aborted
      }
    })();
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  // Video, docs, and dotted lines all appear after the first user message
  const showVideo = messages.length >= 1;
  const showLine = messages.length >= 1;
  const showDocs = messages.length >= 1;
  // Right-side outputs appear once the AI has produced its fix (final assistant reply)
  const showChart = messages.length >= 3;
  const showMemory = messages.length >= 4;
  const showCursor = typing.length > 0;

  return (
    <div className="fade-rise relative mt-10 flex w-full max-w-[320px] flex-1 flex-col items-center" style={{ animationDelay: "1.9s" }}>
      {/* Dotted curve — docs pocket (center) → merge point at chat edge (rendered BEFORE pocket so it sits behind) */}
      <svg
        className={`pointer-events-none absolute right-full ${
          showLine ? "dotted-wipe" : ""
        }`}
        style={{
          top: 220,
          marginRight: 0,
          opacity: showLine ? 1 : 0,
          animationDelay: "0.25s",
          overflow: "visible",
        }}
        width="300"
        height="170"
        viewBox="0 0 300 170"
        aria-hidden
      >
        <path
          className="march-dots"
          d="M 0 166 C 290 166, 10 0, 300 0"
          fill="none"
          stroke="white"
          strokeWidth="5"
          strokeDasharray="2 10"
          strokeLinecap="round"
        />
      </svg>

      {/* Company manuals pocket — on the left, under the Live customer view video */}
      <div
        className={`group absolute right-full transition-all duration-700 ease-out ${
          showDocs ? "translate-x-0 opacity-100" : "-translate-x-2 opacity-0"
        }`}
        style={{ top: 260, marginRight: 160 }}
      >
        <div className="relative" style={{ width: 232, height: 208 }}>
          {/* Back: dark blue pocket — smaller, sits behind */}
          <div
            className="absolute rounded-[20px] transition-transform duration-400 ease-out group-hover:scale-[1.03]"
            style={{
              backgroundColor: "#3B6793",
              width: 202,
              height: 92,
              top: 60,
              left: 15,
              transformOrigin: "50% 100%",
            }}
          />
          {/* Two white documents, rotated opposite ways */}
          <div
            className="doc-page absolute rounded-lg bg-white"
            style={{
              width: 116,
              height: 116,
              top: 18,
              left: 30,
              boxShadow: "0 8px 22px rgba(50,72,93,0.25)",
              ["--doc-rotate" as string]: "-9deg",
              ["--doc-shift-x" as string]: "-14px",
              ["--doc-shift-y" as string]: "-10px",
              ["--doc-rotate-hover" as string]: "-15deg",
            }}
          />
          <div
            className="doc-page absolute rounded-lg bg-white"
            style={{
              width: 116,
              height: 116,
              top: 18,
              left: 86,
              boxShadow: "0 8px 22px rgba(50,72,93,0.25)",
              ["--doc-rotate" as string]: "9deg",
              ["--doc-shift-x" as string]: "14px",
              ["--doc-shift-y" as string]: "-10px",
              ["--doc-rotate-hover" as string]: "15deg",
            }}
          />
          {/* Front: lighter blue pocket cover (perspective-warped SVG) */}
          <img
            src="/folder-cover.svg"
            alt=""
            aria-hidden
            className="absolute bottom-0 left-0 right-0 w-full transition-transform duration-400 ease-out group-hover:scale-[1.03]"
            style={{ height: 140, transformOrigin: "50% 100%" }}
          />
        </div>
        <p
          className="mt-3 text-center text-lg font-semibold text-white"
          style={{ letterSpacing: "-0.035em" }}
        >
          Company manuals
        </p>
      </div>

      {/* Dotted curve — video (middle) → chat */}
      <svg
        className={`pointer-events-none absolute right-full ${
          showLine ? "dotted-wipe" : ""
        }`}
        style={{ top: 100, marginRight: 0, opacity: showLine ? 1 : 0 }}
        width="200"
        height="180"
        viewBox="0 0 200 180"
        aria-hidden
      >
        <path
          className="march-dots"
          d="M 4 10 C 190 10, 10 178, 198 178"
          fill="none"
          stroke="white"
          strokeWidth="5"
          strokeDasharray="2 10"
          strokeLinecap="round"
        />
      </svg>

      {/* Side video panel */}
      <div
        className={`absolute right-full transition-all duration-700 ease-out ${
          showVideo ? "translate-x-0 opacity-100" : "-translate-x-2 opacity-0"
        }`}
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

      {/* Right-side outputs (chart + memory) — curves converge at chat right-edge */}
      {/* Curve: chat → chart card (up-and-right S) */}
      <svg
        className={`pointer-events-none absolute left-full ${
          showChart ? "dotted-wipe" : ""
        }`}
        style={{
          top: 100,
          marginLeft: 0,
          opacity: showChart ? 1 : 0,
          overflow: "visible",
        }}
        width="240"
        height="180"
        viewBox="0 0 240 180"
        aria-hidden
      >
        <path
          className="march-dots"
          d="M 0 178 C 230 178, 10 10, 240 10"
          fill="none"
          stroke="white"
          strokeWidth="5"
          strokeDasharray="2 10"
          strokeLinecap="round"
        />
      </svg>

      {/* Curve: chat → memory card (down-and-right S) */}
      <svg
        className={`pointer-events-none absolute left-full ${
          showMemory ? "dotted-wipe" : ""
        }`}
        style={{
          top: 220,
          marginLeft: 0,
          opacity: showMemory ? 1 : 0,
          animationDelay: "0.25s",
          overflow: "visible",
        }}
        width="300"
        height="170"
        viewBox="0 0 300 170"
        aria-hidden
      >
        <path
          className="march-dots"
          d="M 0 0 C 290 0, 10 166, 300 166"
          fill="none"
          stroke="white"
          strokeWidth="5"
          strokeDasharray="2 10"
          strokeLinecap="round"
        />
      </svg>

      {/* Chart card */}
      <div
        className={`absolute left-full transition-all duration-700 ease-out ${
          showChart ? "translate-x-0 opacity-100" : "translate-x-2 opacity-0"
        }`}
        style={{ top: 0, marginLeft: 220 }}
      >
        <div
          className="rounded-2xl bg-white p-4 transition-transform duration-300 ease-out hover:scale-[1.04]"
          style={{
            width: 232,
            boxShadow: "0 14px 36px rgba(50,72,93,0.22)",
          }}
        >
          <svg viewBox="0 0 240 140" width="100%" height="120" aria-hidden>
            <text x="6" y="22" fontSize="11" fontWeight="600" fill="#B1C8E0" fontFamily="inherit">80%</text>
            <text x="6" y="72" fontSize="11" fontWeight="600" fill="#B1C8E0" fontFamily="inherit">60%</text>
            <text x="6" y="122" fontSize="11" fontWeight="600" fill="#B1C8E0" fontFamily="inherit">40%</text>
            <line x1="36" y1="18" x2="234" y2="18" stroke="#E6E8EB" strokeWidth="1.5" strokeLinecap="round" />
            <line x1="36" y1="68" x2="234" y2="68" stroke="#E6E8EB" strokeWidth="1.5" strokeLinecap="round" />
            <line x1="36" y1="118" x2="234" y2="118" stroke="#E6E8EB" strokeWidth="1.5" strokeLinecap="round" />
            <path
              className={showChart ? "chart-draw" : ""}
              d="M 40 110 C 70 100, 80 78, 110 76 C 145 74, 155 70, 175 50 C 200 26, 215 22, 230 22"
              fill="none"
              stroke="#6291C0"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <p
          className="mt-3 text-center text-lg font-semibold leading-[1.1] text-white"
          style={{ letterSpacing: "-0.035em" }}
        >
          Agent Performance over time increases
        </p>
      </div>

      {/* Memory card */}
      <div
        className={`absolute left-full transition-all duration-700 ease-out ${
          showMemory ? "translate-x-0 opacity-100" : "-translate-x-2 opacity-0"
        }`}
        style={{ top: 260, marginLeft: 160 }}
      >
        <div
          className="rounded-2xl bg-white p-4 transition-transform duration-300 ease-out hover:scale-[1.04]"
          style={{
            width: 232,
            boxShadow: "0 14px 36px rgba(50,72,93,0.22)",
          }}
        >
          <div className="space-y-1.5 text-[13px] leading-snug">
            {[
              ["Issue", "Blinking red light"],
              ["Cause", "Tank sensor not pressed down"],
              ["Saved fix", "Push back-right corner until it clicks"],
              ["Next answer", "Starts with this step"],
            ].map(([label, value]) => (
              <div key={label}>
                <p
                  className="font-bold"
                  style={{ color: "#3B6793", letterSpacing: "-0.025em" }}
                >
                  {label}
                </p>
                <p
                  className="font-medium"
                  style={{ color: "#5E8EBE", letterSpacing: "-0.02em" }}
                >
                  {value}
                </p>
              </div>
            ))}
          </div>
        </div>
        <p
          className="mt-3 text-center text-lg font-semibold text-white"
          style={{ letterSpacing: "-0.035em" }}
        >
          Repair Memory Updated
        </p>
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
              Clutch
            </span>
          </div>
          <div className="flex items-center gap-3">
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
              <path d="M12 12.002H12.5M19 12.002H19.5M5 12.002H5.5M12 13.002C12.5523 13.002 13 12.5542 13 12.002C13 11.4497 12.5523 11.002 12 11.002C11.4477 11.002 11 11.4497 11 12.002C11 12.5542 11.4477 13.002 12 13.002ZM19 13.002C19.5523 13.002 20 12.5542 20 12.002C20 11.4497 19.5523 11.002 19 11.002C18.4477 11.002 18 11.4497 18 12.002C18 12.5542 18.4477 13.002 19 13.002ZM5 13.002C5.55228 13.002 6 12.5542 6 12.002C6 11.4497 5.55228 11.002 5 11.002C4.44772 11.002 4 11.4497 4 12.002C4 12.5542 4.44772 13.002 5 13.002Z" />
            </svg>
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
              <path d="M8.00001 3.09779C8.00001 3.09779 4.03375 2.74194 3.38784 3.38785C2.74192 4.03375 3.09784 8 3.09784 8" />
              <path d="M8.00001 20.9022C8.00001 20.9022 4.03375 21.2581 3.38784 20.6122C2.74192 19.9662 3.09784 16 3.09784 16" />
              <path d="M16 3.09779C16 3.09779 19.9663 2.74194 20.6122 3.38785C21.2581 4.03375 20.9022 8 20.9022 8" />
              <path d="M16 20.9022C16 20.9022 19.9663 21.2581 20.6122 20.6122C21.2581 19.9662 20.9022 16 20.9022 16" />
              <path d="M14.0107 9.99847L20.0625 3.94678" />
              <path d="M9.99696 14.0024L3.63966 20.3807" />
              <path d="M9.99732 10.0024L3.84571 3.85889" />
              <path d="M13.9795 14.0024L20.5279 20.4983" />
            </svg>
          </div>
        </div>
        <div className="mx-6 mt-5 h-px rounded-full" style={{ backgroundColor: "#F9FAFA" }} />

        {mode === "voice" ? (
          <VoiceMode
            messages={messages}
            streamedUser={streamedUser}
            streamedAI={streamedAI}
          />
        ) : (
          <>
        {/* Messages */}
        <div
          className="flex flex-col gap-2.5 px-6 pt-3 font-medium"
          style={{
            letterSpacing: "-0.025em",
            transform: "translateZ(55px)",
            transformStyle: "preserve-3d",
          }}
        >
          {messages.map((m, i) =>
            m.sender === "user" ? (
              <div
                key={i}
                className="msg-pop w-fit max-w-[65%] self-end rounded-2xl px-3 py-2 text-left text-[0.8125rem] leading-[1.15] text-white [text-wrap:pretty]"
                style={{ backgroundColor: "#6291C0" }}
              >
                {m.text}
              </div>
            ) : (
              <div
                key={i}
                className="msg-pop w-fit max-w-[65%] self-start rounded-2xl rounded-bl-none px-3 py-2 text-left text-[0.8125rem] leading-[1.15] [text-wrap:pretty]"
                style={{ backgroundColor: "#E6E8EB", color: "#32485D" }}
              >
                {m.text}
              </div>
            )
          )}
        </div>

        {/* Input bar */}
        <div
          className="mx-5 mt-auto mb-5 flex flex-col gap-2 rounded-xl px-3.5 pt-3 pb-3"
          style={{
            border: "1.5px solid #E6E8EB",
            transform: "translateZ(35px)",
            transformStyle: "preserve-3d",
          }}
        >
          <div
            className="min-h-[1.4em] text-left text-[0.8125rem] [text-wrap:pretty]"
            style={{
              color: typing ? "#32485D" : "#9CA3AF",
              letterSpacing: "-0.02em",
            }}
          >
            {typing || "Ask me something.."}
            {showCursor && <span className="typing-caret">|</span>}
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3" style={{ color: "#B1C8E0" }}>
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
                <path d="M5.82338 12L4.27922 10.4558C2.57359 8.75022 2.57359 5.98485 4.27922 4.27922C5.98485 2.57359 8.75022 2.57359 10.4558 4.27922L19.7208 13.5442C21.4264 15.2498 21.4264 18.0152 19.7208 19.7208C18.0152 21.4264 15.2498 21.4264 13.5442 19.7208L10.0698 16.2464C9.00379 15.1804 9.00379 13.4521 10.0698 12.386C11.1358 11.32 12.8642 11.32 13.9302 12.386L15.8604 14.3162" />
              </svg>
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                width="22"
                height="22"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M17 7V11C17 13.7614 14.7614 16 12 16C9.23858 16 7 13.7614 7 11V7C7 4.23858 9.23858 2 12 2C14.7614 2 17 4.23858 17 7Z" />
                <path d="M17 7H14M17 11H14" strokeLinecap="round" />
                <path
                  d="M20 11C20 15.4183 16.4183 19 12 19M12 19C7.58172 19 4 15.4183 4 11M12 19V22M12 22H15M12 22H9"
                  strokeLinecap="round"
                />
              </svg>
            </div>
            <div className="flex items-center gap-3">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                width="22"
                height="22"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                style={{ color: "#B1C8E0" }}
              >
                <path
                  d="M2 8C2 8 6.47715 3 12 3C17.5228 3 22 8 22 8"
                  strokeLinecap="round"
                />
                <path d="M21.544 13.045C21.848 13.4713 22 13.6845 22 14C22 14.3155 21.848 14.5287 21.544 14.955C20.1779 16.8706 16.6892 21 12 21C7.31078 21 3.8221 16.8706 2.45604 14.955C2.15201 14.5287 2 14.3155 2 14C2 13.6845 2.15201 13.4713 2.45604 13.045C3.8221 11.1294 7.31078 7 12 7C16.6892 7 20.1779 11.1294 21.544 13.045Z" />
                <path d="M15 14C15 12.3431 13.6569 11 12 11C10.3431 11 9 12.3431 9 14C9 15.6569 10.3431 17 12 17C13.6569 17 15 15.6569 15 14Z" />
              </svg>
              <button
                type="button"
                className="send-btn flex h-9 w-9 items-center justify-center rounded-full text-white"
                style={{ backgroundColor: "#6291C0" }}
                key={pulse}
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
        </div>
          </>
        )}
      </TiltedCard>

      {/* Chat / Voice mode toggle */}
      <div
        className="mt-5 flex rounded-full p-1 backdrop-blur"
        style={{ backgroundColor: "rgba(255,255,255,0.18)" }}
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
    </div>
  );
}

function VoiceMode({
  messages,
  streamedUser,
  streamedAI,
}: {
  messages: Msg[];
  streamedUser: string;
  streamedAI: string;
}) {
  let latestUserIdx = -1;
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].sender === "user") latestUserIdx = i;
  }
  const committedUser = latestUserIdx >= 0 ? messages[latestUserIdx].text : "";
  const userText = streamedUser || committedUser;
  const aiText = streamedAI;

  const agentState = aiText ? "talking" : userText ? "listening" : "thinking";

  return (
    <div
      className="flex flex-1 flex-col items-center justify-center px-8 pb-10"
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
      <div className="mt-8 min-h-[3em] w-full max-w-[240px] px-2 text-center">
        {aiText ? (
          <p
            className="text-[0.75rem] font-medium italic leading-[1.3] [text-wrap:pretty]"
            style={{ color: "#7A8BA0", letterSpacing: "-0.01em" }}
          >
            &ldquo;{aiText}&rdquo;
          </p>
        ) : userText ? (
          <p
            className="text-[0.75rem] font-semibold leading-[1.25] [text-wrap:pretty]"
            style={{ color: "#32485D", letterSpacing: "-0.02em" }}
          >
            {userText}
          </p>
        ) : null}
      </div>
    </div>
  );
}
