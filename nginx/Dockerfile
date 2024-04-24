FROM node:17.9.0 as bublik-ui-builder

ARG URL_PREFIX
ENV PNPM_HOME="/root/.local/share/pnpm"
ENV PATH="${PATH}:${PNPM_HOME}"

RUN npm install --global pnpm

RUN pnpm add -g nx

WORKDIR /app

COPY bublik-ui/package.json bublik-ui/pnpm-lock.yaml ./

RUN pnpm install

COPY bublik-ui .

RUN nx run bublik:build:docker --base ${URL_PREFIX}/v2

FROM nginx:1.21-alpine

RUN rm /etc/nginx/conf.d/default.conf

COPY --from=bublik-ui-builder /app/dist/apps/bublik-docker /etc/nginx/html