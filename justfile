set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# All tests (needs Docker)
test:
    uv run --directory ingestor pytest

# Tests without the real TimeTagger container
test-fast:
    uv run --directory ingestor pytest -m "not integration"

up:
    docker compose up -d --build

down:
    docker compose down

logs service="ingestor":
    docker compose logs -f {{service}}
