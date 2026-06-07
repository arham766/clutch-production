"use client";

import { useEffect, useState } from "react";

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

const START_DELAY = 2900; // ms after mount before sequence begins
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

export function ChatDemo() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [typing, setTyping] = useState("");
  const [pulse, setPulse] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    const { signal } = ctrl;

    (async () => {
      try {
        await wait(START_DELAY, signal);
        for (const msg of SCRIPT) {
          if (msg.sender === "user") {
            for (let i = 1; i <= msg.text.length; i++) {
              setTyping(msg.text.slice(0, i));
              await wait(TYPE_MS, signal);
            }
            await wait(AFTER_TYPE_PAUSE, signal);
            setPulse((p) => p + 1);
            await wait(160, signal);
            setTyping("");
            setMessages((m) => [...m, msg]);
            await wait(POST_SEND_GAP, signal);
          } else {
            await wait(AI_THINK, signal);
            setMessages((m) => [...m, msg]);
            await wait(AI_PAUSE, signal);
          }
        }
      } catch {
        // aborted on unmount
      }
    })();

    return () => ctrl.abort();
  }, []);

  const showCursor = typing.length > 0;

  return (
    <>
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
              className="msg-pop w-fit max-w-[65%] self-start rounded-2xl rounded-bl-none px-4 py-3 text-left text-[0.8125rem] leading-[1.15] [text-wrap:pretty]"
              style={{ backgroundColor: "#E6E8EB", color: "#32485D" }}
            >
              {m.text}
            </div>
          )
        )}
      </div>
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
              data-pulse={pulse}
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
  );
}
