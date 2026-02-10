FROM python:3.12-slim

WORKDIR /app

# Install the agent
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Config will be mounted or passed via env
RUN mkdir -p /root/.openclaw-agent

CMD ["openclaw-agent", "run"]
