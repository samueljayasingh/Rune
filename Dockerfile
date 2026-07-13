FROM python:3.12-slim

# Install system dependencies needed by some Python packages (crawl4ai, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files needed for installation
COPY pyproject.toml ./
COPY src/ ./src/

# Install the package and dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Bake in the default workspace so the image boots standalone (no bind mount required).
# docker-compose still bind-mounts ./default_workspace over this for local dev.
COPY default_workspace/ /workspace/

VOLUME ["/workspace"]

EXPOSE 8000

# Run the rune server, pointing at the mounted workspace
ENTRYPOINT ["rune", "--workspace", "/workspace", "server"]
