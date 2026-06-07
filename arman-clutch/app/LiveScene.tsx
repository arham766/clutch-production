"use client";

import { useEffect, useRef, useState } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useLocalParticipant,
  useRoomContext,
  useTracks,
  useTranscriptions,
} from "@livekit/components-react";
import { RoomEvent, Track } from "livekit-client";
import "@livekit/components-styles";
import { Orb } from "@/components/ui/orb";
import { TiltedCard } from "./TiltedCard";

// Live version of the hero scene: real camera feed ("Live customer view") + the real transcript,
// driven by our agent-py worker. Auto-connects on mount (publishes mic + camera = See mode).

type Conn = { serverUrl: string; participantToken: string };
type Msg = { sender: "user" | "ai"; text: string };

export function LiveScene() {
  const [conn, setConn] = useState<Conn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const [runId, setRunId] = useState(0);

  useEffect(() => {
    if (stopped) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/connection-details", { method: "POST" });
        if (!res.ok) throw new Error(await res.text());
        const d = await res.json();
        if (!cancelled) setConn({ serverUrl: d.serverUrl, participantToken: d.participantToken });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "could not connect");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, stopped]);

  const start = () => {
    setError(null);
    setConn(null);
    setStopped(false);
    setRunId((x) => x + 1);
  };

  if (stopped) {
    return (
      <SceneLayout
        mode="voice"
        setMode={() => {}}
        messages={[]}
        agentState="thinking"
        caption=""
        camera={<CameraPlaceholder label="Session ended" />}
        action={{ label: "Start", onClick: start }}
      />
    );
  }

  if (!conn) {
    return (
      <SceneLayout
        mode="voice"
        setMode={() => {}}
        messages={[]}
        agentState="thinking"
        caption=""
        camera={<CameraPlaceholder label={error ? "Couldn't connect" : "Starting…"} />}
        action={error ? { label: "Retry", onClick: start } : undefined}
      />
    );
  }

  return (
    <LiveKitRoom
      serverUrl={conn.serverUrl}
      token={conn.participantToken}
      connect
      audio
      video
      className="contents"
      onDisconnected={() => {
        setStopped(true);
        setConn(null);
      }}
    >
      <RoomAudioRenderer />
      <LiveInner />
    </LiveKitRoom>
  );
}

function LiveInner() {
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const cameraTracks = useTracks([Track.Source.Camera], { onlySubscribed: false });
  const localCam = cameraTracks.find((t) => t.participant.isLocal);
  const segments = useTranscriptions();

  const [mode, setMode] = useState<"chat" | "voice">("voice");
  const [voiceState, setVoiceState] = useState("listening");

  // Ensure mic + camera publish even if the first auto-attempt raced the permission prompt.
  useEffect(() => {
    if (!localParticipant) return;
    localParticipant.setMicrophoneEnabled(true).catch(() => {});
    localParticipant.setCameraEnabled(true).catch(() => {});
  }, [localParticipant]);

  // Agent voice state (for the orb) from the worker's "clutch" data events.
  useEffect(() => {
    if (!room) return;
    const dec = new TextDecoder();
    const onData = (payload: Uint8Array) => {
      try {
        const m = JSON.parse(dec.decode(payload));
        if (m.type === "voice_state") setVoiceState(m.state || "listening");
      } catch {
        /* ignore */
      }
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
    };
  }, [room]);

  // Real transcript: each text-stream segment, attributed user vs agent by identity.
  const localId = localParticipant?.identity;
  const messages: Msg[] = segments
    .filter((s) => s.text?.trim())
    .map((s) => ({
      sender: s.participantInfo.identity === localId ? "user" : "ai",
      text: s.text,
    }));

  const agentState =
    voiceState === "speaking" ? "talking" : voiceState === "thinking" ? "thinking" : "listening";
  const lastAi = [...messages].reverse().find((m) => m.sender === "ai")?.text ?? "";

  const cam =
    localCam && localCam.publication ? (
      <VideoTrack trackRef={localCam} className="h-full w-full object-cover" />
    ) : (
      <CameraPlaceholder label="Allow camera + mic" />
    );

  return (
    <SceneLayout
      mode={mode}
      setMode={setMode}
      messages={messages}
      agentState={agentState}
      caption={lastAi}
      camera={cam}
      action={{ label: "Stop", onClick: () => room.disconnect() }}
    />
  );
}

function CameraPlaceholder({ label }: { label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center bg-[#0E1726] text-xs text-white/60">
      {label}
    </div>
  );
}

function SceneLayout({
  mode,
  setMode,
  messages,
  agentState,
  caption,
  camera,
  action,
}: {
  mode: "chat" | "voice";
  setMode: (m: "chat" | "voice") => void;
  messages: Msg[];
  agentState: "talking" | "listening" | "thinking";
  caption: string;
  camera: React.ReactNode;
  action?: { label: string; onClick: () => void };
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length]);

  return (
    <div
      className="fade-rise relative mt-10 flex w-full max-w-[320px] flex-1 flex-col items-center"
      style={{ animationDelay: "0.2s" }}
    >
      {/* Chat / Voice mode toggle */}
      <div
        className="mb-4 flex rounded-full p-1 backdrop-blur"
        style={{ backgroundColor: "rgba(255,255,255,0.18)" }}
      >
        {(["chat", "voice"] as const).map((m) => (
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
          <span className="text-base font-semibold" style={{ letterSpacing: "-0.02em", color: "#32485D" }}>
            Clutch
          </span>
          <span className="text-xs font-semibold" style={{ color: "#B1C8E0" }}>
            {agentState === "talking" ? "Speaking" : agentState === "thinking" ? "Thinking" : "Listening"}
          </span>
        </div>
        <div className="mx-6 mt-5 h-px rounded-full" style={{ backgroundColor: "#F9FAFA" }} />

        {mode === "voice" ? (
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
              {caption ? (
                <p
                  className="text-[0.75rem] font-medium italic leading-[1.3] [text-wrap:pretty]"
                  style={{ color: "#7A8BA0", letterSpacing: "-0.01em" }}
                >
                  &ldquo;{caption}&rdquo;
                </p>
              ) : (
                <p className="text-[0.75rem] font-medium" style={{ color: "#B1C8E0" }}>
                  Point your camera at the device and ask.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div
            ref={scrollRef}
            className="flex flex-1 flex-col gap-2.5 overflow-y-auto px-6 pt-3 pb-5 font-medium [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
            style={{ letterSpacing: "-0.025em", transform: "translateZ(55px)", transformStyle: "preserve-3d" }}
          >
            {messages.length === 0 && (
              <p className="mt-2 text-[0.8125rem]" style={{ color: "#9CA3AF" }}>
                Ask me something…
              </p>
            )}
            {messages.map((m, i) =>
              m.sender === "user" ? (
                <div
                  key={i}
                  className="msg-pop w-fit max-w-[75%] self-end rounded-2xl px-3 py-2 text-left text-[0.8125rem] leading-[1.15] text-white [text-wrap:pretty]"
                  style={{ backgroundColor: "#6291C0" }}
                >
                  {m.text}
                </div>
              ) : (
                <div
                  key={i}
                  className="msg-pop w-fit max-w-[75%] self-start rounded-2xl rounded-bl-none px-3 py-2 text-left text-[0.8125rem] leading-[1.15] [text-wrap:pretty]"
                  style={{ backgroundColor: "#E6E8EB", color: "#32485D" }}
                >
                  {m.text}
                </div>
              )
            )}
          </div>
        )}
      </TiltedCard>

      {/* Live customer view — the real camera feed, underneath the chat */}
      <div className="mt-5 w-full">
        <div
          className="mx-auto overflow-hidden rounded-2xl bg-white transition-transform duration-300 ease-out hover:scale-[1.03]"
          style={{ width: "100%", maxWidth: 300, height: 180, boxShadow: "0 14px 36px rgba(50,72,93,0.22)" }}
        >
          {camera}
        </div>
        <p
          className="mt-3 text-center text-lg font-semibold text-white"
          style={{ letterSpacing: "-0.035em" }}
        >
          Live customer view
        </p>
      </div>

      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-4 rounded-xl px-7 py-2 text-sm font-semibold text-white transition-transform duration-200 hover:scale-[1.06] active:scale-95"
          style={{ backgroundColor: "#91BDDB", letterSpacing: "-0.02em" }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
