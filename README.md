# Clutch — AI Support Built for Hardware

Clutch is a customer-service agent specifically designed for hardware companies. Unlike traditional software chatbots, Clutch handles physical problems by utilizing a live camera feed. It embeds directly onto a company's support page as a seamless widget offering three powerful modes:

- **Type**: A standard chatbox for quick, precise answers.
- **Talk**: A live voice conversation with an AI agent.
- **See**: A live camera session where the customer shows their physical device, and the agent identifies it and talks them through the fix in real-time.

It achieves flawless accuracy because the reasoning brain is strictly grounded in the **company's own official documentation**.

---

## High Level Design (HLD) & Architecture

*Designed by Fardin and Abdul.*

The Clutch system consists of two primary operational flows:

### 1. Company Onboarding (Setup Flow)
The setup process is entirely self-serve and heavily relies on processing massive corporate manuals:
1. **Upload**: An enterprise user logs in and uploads product manuals, spec sheets, and guides.
2. **Parsing**: Documents are parsed, chunked, and formatted, extracting vital metadata and imagery.
3. **Indexing**: The parsed text is ingested into the semantic index for real-time retrieval.
4. **Catalog Generation**: A visual closed-set product catalog is derived, which serves as the reference database for the vision model.
5. **Deployment**: Clutch generates a simple `<script>` snippet containing the user's public embed key.

### 2. Customer Support Session (Runtime Flow)
When a customer interacts with the widget on the company's site:
- **Text & Voice Mode**: The user asks a question, and the agent uses semantic retrieval across the company's corpus to generate a strictly grounded, accurate answer.
- **See Mode (Vision)**: The customer initiates a camera feed. 
  - **Identify**: The vision model processes the video frames against the closed-set catalog to accurately identify *which* product the user has and *what* physical problem is visible.
  - **Retrieve & Resolve**: Once identified, semantic retrieval is tightly scoped to that specific product, pulling the exact fix from the manual. The agent walks the user through the resolution over a low-latency WebRTC connection.

### System Diagram
```mermaid
graph TD
    subgraph Company Setup
        Docs[Company Manuals] -->|Parsed by Unsiloed| Index[(Moss Index)]
        Docs --> Catalog[Product Catalog]
    end

    subgraph Runtime (Embedded Widget)
        Customer[Customer Support Page] -->|Embed Snippet| LiveKit[LiveKit WebRTC Room]
        LiveKit -->|Frames| Vision[Vision Model: Qwen-VL]
        Vision -->|Product + Problem| Grounding[Semantic Retrieval]
        Grounding --> Index
        Grounding --> Agent[Reasoning Brain: MiniMax]
        Agent -->|Text / Voice| LiveKit
    end
    
    subgraph AI Gateway Routing
        Vision -.->|Managed via| TrueFoundry[TrueFoundry AI Gateway]
        Agent -.->|Managed via| TrueFoundry
    end
```

---

## Sponsor Integrations & Stack

Clutch leverages the very best modern AI tools to achieve sub-second reasoning and multimodal I/O:

| Sponsor | Technology | Use Case in Clutch |
|---------|------------|---------------------|
| **LiveKit** | Transport / RTC | Provides the low-latency WebRTC infrastructure for Chat, Voice, and Live Video transport directly inside the embeddable React widget. |
| **MiniMax** | Reasoning Engine | The primary LLM brain. Responsible for taking retrieved context and synthesizing accurate, conversational instructions without improvising outside the manuals. |
| **Qwen** | Vision / Perception | Utilizes Qwen-VL to process live camera frames, cross-reference them with the product catalog, and identify both the hardware model and the visual problem (e.g., a blinking error light). |
| **Cartesia** | Voice I/O | Handles instantaneous Speech-to-Text (STT) and Text-to-Speech (TTS) via LiveKit plugins, ensuring the agent sounds human and responds in real-time. |
| **Unsiloed** | Document Parsing | Parses complex, unstructured PDF manuals and spec sheets into clean, semantically rich chunks optimized for RAG. |
| **Moss** | Semantic Retrieval | Serves as the vector index and real-time semantic search engine over the company corpus, guaranteeing the agent is grounded in factual truth. |
| **TrueFoundry** | AI Gateway | Routes all model calls (MiniMax, Qwen). Acts as the enterprise governance layer, providing cost tracking, fallbacks, and strict guardrails. |

---

## Repository Structure (Monorepo)

This repository is split into two deployable services, fully configured for Render.

```
clutch-production/
├── frontend/             # Next.js 15 + Tailwind v4 Web Application
│   ├── app/              # Frontend pages, API routes, and components
│   ├── public/           # Static assets, fonts, logos, and the mock video
│   └── package.json      # Node.js dependencies
│
├── backend/              # FastAPI Application
│   ├── src/              # Core domain logic (API, Db, Onboarding, Perception)
│   ├── pyproject.toml    # Python dependencies
│   └── render-build.sh   # Render build script
│
├── render.yaml           # Infrastructure-as-Code Blueprint
└── README.md             # This document
```

---

## Local Development Setup

### Backend (FastAPI)
1. `cd backend`
2. Set up your virtual environment: `python -m venv .venv` and activate it.
3. Install dependencies: `pip install -e .`
4. Copy `.env.example` to `.env` and fill in your Cloudflare R2, TrueFoundry, and Firebase credentials.
5. Provide your `firebase-sa.json` service account file in the `backend` root.
6. Start the server: `uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload`

### Frontend (Next.js)
1. `cd frontend`
2. Install dependencies: `npm install`
3. Configure your API route if needed (default points to `http://localhost:8000/api`).
4. Start the dev server: `npm run dev`
5. Open `http://localhost:3000`.

---

## Deployment (Render)

This monorepo is fully configured to be deployed on Render using the included `render.yaml` Blueprint.
1. Push this repository to GitHub.
2. In the Render Dashboard, click **New > Blueprint**.
3. Connect the repository. Render will automatically spin up both the **clutch-frontend** (Node.js Web Service) and **clutch-backend** (Python Web Service).
4. Supply the required environment variables marked as `sync: false` in the dashboard.
5. In the frontend environment variables, set `NEXT_PUBLIC_API_URL` to your newly deployed backend URL (e.g., `https://clutch-backend.onrender.com/api`).
