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

# The workspace (agents, skills, memories, config) is volume-mounted at runtime.
# Default workspace is provided in the repo and mounted by docker-compose.
VOLUME ["/workspace"]

EXPOSE 8000

# Run the rune server, pointing at the mounted workspace
ENTRYPOINT ["rune", "--workspace", "/workspace", "server"]
