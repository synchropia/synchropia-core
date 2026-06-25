# Synchropia: Swarm Orchestration & Agentic Incident Management Platform

Synchropia is a state-of-the-art multi-agent swarm orchestration and incident management platform designed to automate complex engineering, data pipeline, and system architecture tasks. By leveraging an ecosystem of specialized agents, real-time message streaming via Apache Kafka, and standard integration hooks, Synchropia acts as an autonomous digital workforce that seamlessly translates high-level prompts into blueprints, codebase refactors, ETL pipelines, and infrastructure deployments.

---

## Key Capabilities

*   **Autonomous Agent Swarms**: Dynamic coordination of role-specific agents (Product Managers, Architects, Developers, Data Engineers, and MLOps professionals) to solve multi-stage engineering problems.
*   **Unified LLM Gateway**: A centralized gateway supporting multiple enterprise foundation models (Google Gemini, Groq, Mistral, Cerebras, NVIDIA NIM, SambaNova, OpenRouter, and more) with unified token streaming.
*   **Integrated Action Tools**: Native connections to standard enterprise platforms (Slack, Jira, GitHub, GitLab, and Mem0) enabling agents to read issues, write pull requests, report statuses, and persist context.
*   **Robust Sandbox Execution**: Isolated code execution backends (supporting both secure remote sandbox environments and local PowerShell fallback fallbacks) for executing agent commands and compiling outputs safely.
*   **Real-time Event Streaming**: Event-driven token streaming powered by FastAPI Server-Sent Events (SSE) and Apache Kafka for ultra-low latency typewriter feedback loops.
*   **Advanced Caching & DB Storage**: High-performance semantic caching layer backed by Redis and SQL database models managed with Alembic.

---

## Directory Structure

```
synchropia/
├── alembic/                # Database migrations and revision scripts
├── init-scripts/           # Database initialization scripts
├── src/                    # Core application source code
│   ├── agents/             # Swarm Agent implementation modules
│   │   ├── architect/      # System architecture & blueprinting agent
│   │   ├── data_engineering/ # ETL/ELT, ML pipeline, and data science agents
│   │   │   ├── ai/
│   │   │   ├── data_engineer/
│   │   │   ├── data_scientist/
│   │   │   └── mlops/
│   │   ├── development/    # Application engineering swarm agents
│   │   │   ├── backend/
│   │   │   ├── devops/
│   │   │   ├── frontend/
│   │   │   └── qa/
│   │   └── orchestrator/   # Swarm management and coordination agents
│   │       └── pm/         # Product Manager orchestrator agent
│   ├── common/             # Common middlewares and cross-cutting concerns
│   ├── config/             # Environment settings and app configurations
│   ├── core/               # Unified LLM Gateway and foundation model orchestrators
│   ├── deps/               # Authentication and endpoint dependency injectors
│   ├── models/             # Database tables (SQLAlchemy) and tool schemas
│   ├── routes/             # Feature-specific router packages
│   ├── services/           # External service connectors (Slack, GitHub, Jira, etc.)
│   └── utils/              # Helper utilities (Loggers, rate limiters, retries)
├── alembic.ini             # Alembic database migration configuration
├── docker-compose.yaml     # Swarm environment configuration (Kafka, Redis, DB)
├── justfile                # Unified task Runner for developer environments
├── main.py                 # FastAPI application server and lifespan manager
├── pyproject.toml          # Package metadata and system dependencies
└── uv.lock                 # Sealed dependency locking file
```

---

## Architecture Overview

### Swarm Orchestration
At the core of the platform is the **Orchestrator Swarm**, which coordinates actions between specialized developer agents. Requests are evaluated by a Product Manager Agent, which plans the workload, delegates sub-tasks to backend developers, system architects, and MLOps modules, and checks outputs against strict quality guardrails before returning them.

### High-performance Event Pipeline
*   **Unified LLM Gateway**: Standardizes requests to all providers, wrapping their individual APIs into single unified JSON payloads.
*   **Kafka & SSE Event Bridge**: During model generations, token chunks are immediately published to a Kafka topic. A FastAPI SSE router consumes this topic in real-time, streaming tokens directly to the client's typewriter UI with millisecond latency.
*   **Semantic Cache**: Redis-based semantic cache intercepts gateway requests, matching queries to return cached results instantaneously while simulating real-time typing for a consistent interface experience.
