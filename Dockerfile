FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY config/ config/

RUN pip install --no-cache-dir .

EXPOSE 8000

# Default: replay mode for development
# Override CMD for live mode: ["python", "-m", "chat_to_cop.live", "--server", "ws://..."]
CMD ["python", "-m", "chat_to_cop.replay"]
