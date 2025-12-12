# Multi-stage Dockerfile for library-http-server
# Uses Node 18.19 (matches package.json engines)

FROM node:18.19.0 AS builder
WORKDIR /app

# Install all dependencies (including devDeps needed for build)
COPY package*.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# Runtime image
FROM node:18.19.0
WORKDIR /app
ENV NODE_ENV=production

# Only install production deps in final image
COPY package*.json ./
RUN npm ci --omit=dev

# Copy built artifacts
COPY --from=builder /app/dist ./dist

# Default port used by commented http transport in src/index.ts
EXPOSE 1337

CMD ["node", "dist/index.js"]
