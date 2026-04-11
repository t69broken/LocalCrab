FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nodejs \
    npm \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install clawhub CLI globally
RUN npm install -g clawhub@latest || true

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY src/ ./src/
COPY frontend/ ./frontend/

# Data directories
RUN mkdir -p /app/data/{memory,skills,personas,agents}

EXPOSE 18798

# Add src to Python path
ENV PYTHONPATH=/app/src
ENV MEMORY_DB=/app/data/memory/memory.db
ENV SKILLS_DIR=/app/data/skills
ENV PERSONAS_DIR=/app/data/personas

CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "18798", \
     "--app-dir", "/app/src", \
     "--log-level", "info", \
     "--ws-ping-interval", "30", \
     "--ws-ping-timeout", "10"]
