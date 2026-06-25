# Start all backend services (Redis, PostgreSQL, Kafka, Kafka UI, Schema Registry) in background
start:
    docker-compose up -d

# Stop all background backend services
stop:
    docker-compose down

# Check the status of background services
status:
    docker-compose ps

# Stream logs of background backend services
logs:
    docker-compose logs -f

# Start the FastAPI gateway server in reload mode
serve:
    uv run main.py

# Apply database migrations using Alembic
migrate:
    uv run alembic upgrade head

# sync packages from the pyproject.toml 
sync:
   uv sync