FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application
COPY src/ src/

# Create data directory
RUN mkdir -p data

EXPOSE 8000

# Default: run both API and bot
CMD ["python", "-m", "src.main"]
