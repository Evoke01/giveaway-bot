FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# The .env file is NOT committed — env vars are injected by the platform at runtime.
# SESSION_STRING, API_ID, API_HASH must be set as environment variables.

CMD ["python", "main.py"]
