import { NextResponse } from "next/server";
import { AccessToken, type AccessTokenOptions, type VideoGrant } from "livekit-server-sdk";
import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";

// Live-agent connection minter. Mirrors how our moss-hacker-starter integration dispatches the
// agent-py worker: explicit agent dispatch by name + room metadata {company_key, modality} so the
// worker scopes the session and enables See (audio + camera). Backend-only — no visual code touched.

const LIVEKIT_URL = process.env.LIVEKIT_URL;
const API_KEY = process.env.LIVEKIT_API_KEY;
const API_SECRET = process.env.LIVEKIT_API_SECRET;
const AGENT_NAME = process.env.AGENT_NAME ?? "agent-py";

export const revalidate = 0;

type ConnectionDetails = {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
};

export async function POST() {
  try {
    if (!LIVEKIT_URL) throw new Error("LIVEKIT_URL is not defined");
    if (!API_KEY) throw new Error("LIVEKIT_API_KEY is not defined");
    if (!API_SECRET) throw new Error("LIVEKIT_API_SECRET is not defined");

    const participantName = "user";
    const participantIdentity = `clutch_user_${Math.floor(Math.random() * 100_000)}`;
    const roomName = `clutch_room_${Math.floor(Math.random() * 100_000)}`;

    // Dispatch the worker by name into this room, and stamp the room metadata our worker reads
    // ({company_key, modality}). modality=see → the worker enables audio STT + the See/vision path.
    const roomConfig = new RoomConfiguration({
      agents: [new RoomAgentDispatch({ agentName: AGENT_NAME })],
    });
    roomConfig.metadata = JSON.stringify({ company_key: "demo", modality: "see" });

    const token = await createParticipantToken(
      { identity: participantIdentity, name: participantName },
      roomName,
      roomConfig,
    );

    const data: ConnectionDetails = {
      serverUrl: LIVEKIT_URL,
      roomName,
      participantName,
      participantToken: token,
    };
    return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown error";
    console.error("connection-details:", message);
    return new NextResponse(message, { status: 500 });
  }
}

function createParticipantToken(
  userInfo: AccessTokenOptions,
  roomName: string,
  roomConfig: RoomConfiguration,
): Promise<string> {
  const at = new AccessToken(API_KEY, API_SECRET, { ...userInfo, ttl: "15m" });
  const grant: VideoGrant = {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
  };
  at.addGrant(grant);
  at.roomConfig = roomConfig;
  return at.toJwt();
}
