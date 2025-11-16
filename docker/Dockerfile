FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    iproute2 \
    iputils-ping \
    net-tools \
    tcpdump \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY bin/ ./bin/

# Make CLI executable
RUN chmod +x bin/courier

# Create a symlink for easier access
RUN ln -s /app/bin/courier /usr/local/bin/courier

# Set Python path
ENV PYTHONPATH=/app/src

# Expose default port
EXPOSE 5000/udp

# Default command
CMD ["courier", "recv", "--port", "5000"]