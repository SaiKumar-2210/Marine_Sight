# Stage 1: Build the React frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Final runtime image (Python + Node.js)
FROM python:3.10-slim
WORKDIR /app

# Install system dependencies (OpenCV requires libgl1 and libglib2.0-0)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    libgl1 \
    libglib2.0-0 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python ML dependencies
COPY ml_service/requirements.txt ./ml_service/
RUN pip install --no-cache-dir -r ml_service/requirements.txt

# Install Node production dependencies
COPY package*.json ./
RUN npm ci --omit=dev

# Copy backend and ML service source code
COPY ml_service/ ./ml_service/
COPY server/ ./server/
COPY server.js ./

# Copy built frontend from Stage 1
COPY --from=frontend-builder /app/dist ./dist

# Create a directory for persistent data
RUN mkdir -p /app/data

# Environment configuration
ENV PORT=3000
ENV NODE_ENV=production
ENV PYTHON=python3
ENV MARINESIGHT_DB=/app/data/marinesight.sqlite
ENV MARINESIGHT_CACHE_DIR=/app/data/cache

# Expose backend port
EXPOSE 3000

# Start the Node.js server
CMD ["npm", "start"]
