FROM python:3.12-slim

# System deps needed by some wheels (chromadb/onnxruntime, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libssl3 libffi8 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the project package (editable) so `src.*` imports resolve
COPY . .
RUN pip install --no-cache-dir -e .

# Ensure the data directory exists at runtime
RUN mkdir -p data

# Koyeb sets PORT; default to 8501 for Fly.io / local.
EXPOSE ${PORT:-8501}

ENV STREAMLIT_SERVER_PORT=${PORT:-8501}
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
# Disable the login/signup screen for the public audit deployment.
ENV DASHBOARD_AUTH_DISABLED=1

CMD ["streamlit", "run", "src/dashboard/app.py", \
     "--server.port=${PORT:-8501}", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
