FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/

# Re-install with the actual source (editable-like behavior)
RUN pip install --no-cache-dir .

# Create data directory for SQLite
RUN mkdir -p data

EXPOSE 8000

# Default: run the FastAPI server
# Override for replay: docker run chat-to-cop python -m chat_to_cop.replay /app/data/chat.zip
CMD ["uvicorn", "chat_to_cop.api:app", "--host", "0.0.0.0", "--port", "8000"]
