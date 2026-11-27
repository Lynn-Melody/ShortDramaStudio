FROM node:24-bookworm-slim
WORKDIR /workspace/apps/web
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY apps/web ./
COPY contracts /workspace/contracts
CMD ["pnpm", "dev", "--host", "0.0.0.0"]
