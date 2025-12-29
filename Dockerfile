# Use Python base image
FROM python:3.9-slim

# 1. Install dependencies for downloading and SSL
RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    unzip \
    curl \
    gnupg \
    && apt-get clean

# 2. Download and Install Google Chrome (The Stable Way)
# We download the .deb file directly and let apt handle the dependencies
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && apt-get clean

# 3. Set working directory
WORKDIR /app

# 4. Copy files
COPY . .

# 5. Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 6. Start the server
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "300"]
