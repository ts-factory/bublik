FROM node:22.13-alpine AS frontend-builder

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
ARG URL_PREFIX=""

RUN pnpm add -g nx

WORKDIR /app

COPY ./bublik-ui/package.json ./bublik-ui/pnpm-lock.yaml ./

RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

COPY ./bublik-ui ./

RUN BASE_URL="$URL_PREFIX/v2" nx run bublik:build --base="$URL_PREFIX/v2" --sourcemap="true"

FROM nginx:1.27-alpine

COPY --from=frontend-builder /app/dist/apps/bublik /app/bublik-ui

RUN rm /etc/nginx/conf.d/default.conf
