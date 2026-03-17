FROM python:3.11-slim

WORKDIR /app

# Systempakete für web3/eth-account
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencies zuerst (besseres Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code
COPY . .

# Logs-Verzeichnis erstellen
RUN mkdir -p logs

# Standardmäßig schedule-Modus (läuft dauerhaft)
CMD ["python", "main.py", "schedule-trading"]
