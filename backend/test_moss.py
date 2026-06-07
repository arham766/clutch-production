import os
import asyncio
from src.api.deps import get_config
from src.config import load_config
from moss import MossClient, QueryOptions

cfg = load_config()

async def main():
    client = MossClient(cfg.moss_project_id, cfg.moss_api_key)
    indexes = await client.list_indexes()
    print("Found indexes:")
    for idx in indexes:
        print(" -", idx.name)
        if "clutch" in idx.name:
            print(f"Querying {idx.name} for 'resume'...")
            await client.load_index(idx.name)
            res = await client.query(idx.name, "resume experience skills", QueryOptions(top_k=5))
            for doc in res.docs:
                print(f"\n[Score: {doc.score:.3f}] Source: {doc.metadata.get('source')}")
                print(f"Text snippet: {doc.text[:100]}...")

if __name__ == "__main__":
    asyncio.run(main())
