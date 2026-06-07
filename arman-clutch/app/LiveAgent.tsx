"use client";

import { useEffect, useState } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useLocalParticipant,
  useRoomContext,
  useTracks,
} from "@livekit/components-react";
import { RoomEvent, Track } from "livekit-client";
import "@livekit/components-styles";

// The live Clutch agent (Type/Talk/See over LiveKit → our agent-py worker), surfaced as an overlay.
// It auto-publishes mic + camera (See mode) and renders straight from the worker's event contract:
//   answer       → grounded answer caption (+ citations)
//   voice_state  → listening / thinking / speaking indicator
//   moss_context → live "Knowledge Matches" (the doc chunks that grounded each turn)

const ACCENT = "#5E8EBE";
const INK = "#32485D";

type Conn = { serverUrl: string; participantToken: string };

export function LiveAgent({ onClose }: { onClose: () => void }) {
  const [conn, setConn] = useState<Conn | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
  }, []);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
    >
      <div
        className="relative flex w-full max-w-3xl flex-col overflow-hidden rounded-[28px] bg-white shadow-2xl"
        style={{ height: "min(88vh, 720px)" }}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 z-10 flex h-9 w-9 items-center justify-center rounded-full text-white transition-transform hover:scale-110"
          style={{ backgroundColor: ACCENT }}
        >
          ✕
        </button>

        {error ? (
          <Centered>
            <p className="font-semibold" style={{ color: INK }}>
              Couldn&apos;t reach the agent.
            </p>
            <p className="mt-1 text-sm text-gray-500">{error}</p>
          </Centered>
        ) : !conn ? (
          <Centered>
            <p className="font-semibold" style={{ color: ACCENT }}>
              Connecting…
            </p>
          </Centered>
        ) : (
          <LiveKitRoom
            serverUrl={conn.serverUrl}
            token={conn.participantToken}
            connect
            audio
            video
            onDisconnected={onClose}
            className="flex h-full flex-col"
          >
            <RoomAudioRenderer />
            <SessionUI />
          </LiveKitRoom>
        )}
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full flex-col items-center justify-center text-center">{children}</div>;
}

type Match = { text: string; score?: number; metadata?: { source?: string; section?: string } };

function SessionUI() {
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const cameraTracks = useTracks([Track.Source.Camera], { onlySubscribed: false });
  const localCam = cameraTracks.find((t) => t.participant.isLocal);

  const [voiceState, setVoiceState] = useState<string>("listening");
  const [answer, setAnswer] = useState<string>("");
  const [citations, setCitations] = useState<{ doc?: string; section?: string }[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);

  // Belt-and-suspenders: ensure mic + camera are publishing (LiveKitRoom audio/video already does,
  // but a denied prompt on the first try shouldn't leave us silent if the user re-grants).
  useEffect(() => {
    if (!localParticipant) return;
    localParticipant.setMicrophoneEnabled(true).catch(() => {});
    localParticipant.setCameraEnabled(true).catch(() => {});
  }, [localParticipant]);

  // Render straight from the worker's "clutch" data-channel event contract.
  useEffect(() => {
    if (!room) return;
    const dec = new TextDecoder();
    const onData = (payload: Uint8Array) => {
      try {
        const m = JSON.parse(dec.decode(payload));
        if (m.type === "answer") {
          setAnswer(m.text || "");
          setCitations(Array.isArray(m.citations) ? m.citations : []);
        } else if (m.type === "voice_state") {
          setVoiceState(m.state || "listening");
        } else if (m.type === "moss_context" && m.data) {
          setMatches(Array.isArray(m.data.matches) ? m.data.matches : []);
        }
      } catch {
        // non-JSON / unrelated packet — ignore
      }
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
    };
  }, [room]);

  const stateLabel =
    voiceState === "speaking"
      ? "Speaking…"
      : voiceState === "thinking"
        ? "Thinking…"
        : "Listening — point your camera at the device and ask.";

  return (
    <div className="flex h-full flex-col">
      {/* header */}
      <div className="flex items-center gap-2 px-6 pt-5 pb-3">
        <span className="text-lg font-bold tracking-tight" style={{ color: INK }}>
          Clutch
        </span>
        <span
          className="ml-1 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
          style={{ backgroundColor: "#E6EEF6", color: ACCENT }}
        >
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{
              backgroundColor: voiceState === "speaking" ? "#2BB673" : ACCENT,
              animation: "pulse 1.4s ease-in-out infinite",
            }}
          />
          {voiceState}
        </span>
      </div>

      {/* camera feed (what the agent sees) */}
      <div className="relative mx-6 flex-1 overflow-hidden rounded-2xl bg-[#0E1726]">
        {localCam ? (
          <VideoTrack trackRef={localCam} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-white/60">
            Allow camera + mic to start
          </div>
        )}

        {/* answer caption */}
        {answer && (
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-4">
            <p className="text-sm leading-snug text-white [text-wrap:pretty]">{answer}</p>
            {citations.length > 0 && (
              <p className="mt-1 text-[11px] text-white/70">
                Sources: {citations.map((c) => c.doc || c.section).filter(Boolean).join(" · ")}
              </p>
            )}
          </div>
        )}
      </div>

      {/* status + knowledge matches */}
      <div className="px-6 pt-3 pb-5">
        <p className="text-sm font-medium" style={{ color: INK }}>
          {stateLabel}
        </p>
        {matches.length > 0 && (
          <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
            {matches.slice(0, 4).map((m, i) => (
              <div
                key={i}
                className="min-w-[180px] max-w-[220px] shrink-0 rounded-xl px-3 py-2"
                style={{ backgroundColor: "#F1F5F9" }}
              >
                <div className="mb-1 flex items-center justify-between text-[10px] font-semibold" style={{ color: ACCENT }}>
                  <span className="truncate">{m.metadata?.source || "doc"}</span>
                  {typeof m.score === "number" && <span>{m.score.toFixed(2)}</span>}
                </div>
                <p className="line-clamp-3 text-[11px] leading-tight" style={{ color: INK }}>
                  {m.text}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}`}</style>
    </div>
  );
}
