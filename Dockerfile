FROM python:3.12-slim

# Install system dependencies for PDF and DOC processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    antiword \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install poetry
RUN pip install --no-cache-dir poetry

# Copy project files
COPY pyproject.toml poetry.lock ./
COPY src/ ./src/

# Configure poetry to install to system site-packages
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Add uvicorn and fastapi (should be in dependencies but making sure)
RUN pip install uvicorn fastapi python-multipart

# Expose port
EXPOSE 8080

# Run the service
CMD ["python", "src/likhit/service/main.py"]
