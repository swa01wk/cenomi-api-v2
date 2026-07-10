# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — deps
# Install node_modules in isolation so they are cached separately from source.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS deps

WORKDIR /app

# Copy lockfile first — cache-busts only when dependencies change
COPY package.json package-lock.json ./
RUN npm ci --omit=dev


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — builder
# Runs `next build` to produce the standalone output bundle.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Copy deps from stage 1
COPY --from=deps /app/node_modules ./node_modules

# Copy all source (respects .dockerignore)
COPY . .

# NEXT_PUBLIC_* vars must be known at build time.
# Pass them as build args and set as ENV so next build picks them up.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

# Enable Next.js standalone output — produces a self-contained server bundle
# that does not need node_modules at runtime.
# Add `output: 'standalone'` to next.config.ts (see note below Dockerfile).
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — runtime
# Only the standalone bundle, public assets, and static files.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS runtime

# Non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Copy the standalone server bundle produced by `next build`
COPY --from=builder --chown=appuser:appgroup /app/.next/standalone ./
COPY --from=builder --chown=appuser:appgroup /app/.next/static ./.next/static
COPY --from=builder --chown=appuser:appgroup /app/public ./public

USER appuser

EXPOSE 3000

# ── Health check ─────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget -qO- http://localhost:3000/ || exit 1

# ── Entrypoint ───────────────────────────────────────────────────────────────
CMD ["node", "server.js"]
