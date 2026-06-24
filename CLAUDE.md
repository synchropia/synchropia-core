    # 🤖 Synchropia: Agentic Software Delivery

**"Engineering Order from Complexity."**

Synchropia is a multi-agent autonomous system designed to revolutionize how software is built and deployed. By delegating operational tasks to a specialized swarm of agents, Synchropia minimizes technical debt and maximizes development velocity.

## 🚀 The Agent Squad
* **PM Agent:** Converts high-level business goals into actionable Jira tickets. The main Orchestrator agent overseeing the whole process for both the Software and Data Engineering swarms 
* **Architect Agent:** Generates system blueprints and defines API contracts.
* **DevOps Agent:** Manages cloud infrastructure (AWS) and CI/CD pipelines (GitHub Actions).
* **Developer Agents:** Frontend/Backend/Mobile specialists that write, test, and commit code.
* **QA Agent:** Autonomous tester that validates quality gates via SonarQube.

## Data Engineering 

* **Data Agent** :  Constructing Extract, Transform, and Load (ETL) or ELT workflows to move data from various sources (like web applications or IoT devices) into central storage systems (like data warehouses or data lakes).
* **Data Scientist Agent** : Training machine learning models, interpreting data patterns, and creating predictive algorithms or data-driven insights for business use cases, leveraing Jupyter MCP Server to interact with Jupyter Notebooks to write code for data analysis and model training.
* **MLOps Agent** : Deploys and manages ML models in production, handling tasks like model versioning, monitoring, and automated retraining. Monitors Concept / Data drift 
* **AI Engineer** : Builds apps with LLMs , orchestrates AI Agents , integrates LLMs with existing enterprise systems , Implements RAG workflows , runs evaluations and tests, fine-tuning LLMs for downstream tasks.

## 🛠 Tech Stack
- **Orchestration:** LangChain / LangGraph / deepagents ( go through the pyproject.toml)
- **VCS Integration:** GitHub Apps API
- **Infrastructure:** AWS (IaC via Terraform)
- **Quality Assurance:** SonarQube Cloud

## 🕒 FastAPI Lifespan Events
The lifespan event in FastAPI is written to manage startup and shutdown logic for your application resources (e.g., database connection pools, Redis clients, Kafka producers, background runners, and ML models).

FastAPI provides a unified, recommended approach using Python's `contextlib.asynccontextmanager`. This replaces the deprecated `@app.on_event("startup")` and `@app.on_event("shutdown")` hooks.

### When do you write a lifespan event?
You write a lifespan event when you want to:
* **Initialize connections on startup** (so they are ready when the first request arrives).
* **Dispose of connections cleanly on shutdown** (to prevent connection leaks, orphaned processes, and socket exhaustion).
* **Handle boot-time errors** (e.g., failing to start the server if your Kafka broker or Postgres DB is offline).
* **Share resources globally** across your application endpoints.

*Built for the future of AI-driven engineering.*