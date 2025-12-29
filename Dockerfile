# Use newer Python (Fixes the "End of Life" warning)
FROM python:3.11-slim

# Install system dependencies
# We add 'ffmpeg' so YouTube videos process correctly
RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    ffmpeg \
    && apt-get clean

# Set working directory
WORKDIR /app

# Copy files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Start the server
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "300"]
