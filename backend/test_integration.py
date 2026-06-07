import asyncio
import json
import os
import sys
import time

from livekit import rtc
from dotenv import load_dotenv

def main():
    load_dotenv()
    
    livekit_url = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
    api_key = os.environ.get("LIVEKIT_API_KEY", "devkey")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "secret")
    
    async def run_test():
        from livekit import api
        
        # 1. Mint a token
        print("Minting token for test client...")
        room_name = f"clutch-demo-{int(time.time())}"
        metadata = json.dumps({"company_key": "demo", "modality": "talk"})
        
        token = api.AccessToken(api_key, api_secret) \
            .with_identity("test_client") \
            .with_name("TestClient") \
            .with_grants(api.VideoGrants(room_join=True, room=room_name)) \
            .with_metadata(metadata) \
            .with_attributes({"company_key": "demo", "modality": "talk"})
            
        jwt = token.to_jwt()
        
        # 2. Connect to Room
        room = rtc.Room()
        
        events = []
        
        @room.on("data_received")
        def on_data(packet):
            if getattr(packet, "topic", None) == "clutch":
                try:
                    ev = json.loads(bytes(packet.data).decode("utf-8"))
                    events.append(ev)
                    print(f"[clutch-event] {ev.get('type')}: {str(ev)[:150]}")
                except:
                    pass
                    
        @room.on("track_subscribed")
        def on_track(track, pub, participant):
            print(f"[track] SUBSCRIBED {track.kind} from {participant.identity}")
            
        print(f"Connecting to {livekit_url} room {room_name}...")
        await room.connect(livekit_url, jwt)
        print("Connected! Waiting for agent to join and greet...")
        
        # 3. Wait for the agent to say hello
        greeting = None
        for _ in range(150): # wait up to 15 seconds
            await asyncio.sleep(0.1)
            for ev in events:
                if ev.get("type") == "answer" and "Hi!" in ev.get("text", ""):
                    greeting = ev
                    break
            if greeting:
                break
                
        if greeting:
            print(f"\nSUCCESS! Received greeting: {greeting['text']}")
        else:
            print("\nFAIL: Did not receive greeting from agent.")
            print(f"Agent participants: {[p.identity for p in room.remote_participants.values()]}")
            print(f"Events received: {events}")
            
        await room.disconnect()
        
    asyncio.run(run_test())

if __name__ == "__main__":
    main()
