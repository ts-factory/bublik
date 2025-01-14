###########################################
#         Base Python Image              #
###########################################
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PATH "/app/te/build/inst/default/bin:$PATH"

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  gettext=0.21-12 \
  python3-celery=5.2.6-5 \
  rsync=3.2.7-1 \
  flex=2.6.4-8.2 \
  bison=2:3.8.2* \
  ninja-build=1.11.* \
  libjansson-dev=2.14-2 \
  libjansson-doc=2.14-2 \
  libjansson4=2.14-2 \
  libpopt-dev=1.19* \
  libpcre3-dev=2:8.39-15 \
  pixz=1.0.7-2 \
  libxml-parser-perl=2.46-4 \
  build-essential=12.9 \
  curl=7.88.1-10* \
  libkrb5-dev=1.20.1-2* \
  libffi-dev=3.4.4-1 \
  libxml2-dev=2.9.14* \
  libyaml-dev=0.2.5-1 \
  libssl-dev=3.0.15-1* \
  libglib2.0-dev=2.74.6-2* \
  git=1:2.39.5-0* \
  && rm -rf /var/lib/apt/lists/* \
  && cpan -T JSON

# Install UV
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN chmod +x /uv-installer.sh && /uv-installer.sh && rm /uv-installer.sh
ENV PATH "/root/.local/bin/:$PATH"

# Install dependencies using uv pip
RUN uv pip install --system --no-cache-dir meson==1.6.1 watchfiles==1.0.4

RUN mkdir bublik

COPY ./requirements.txt /app/bublik/requirements.txt
RUN uv pip install --system --no-cache-dir -r /app/bublik/requirements.txt

WORKDIR /app/te
COPY ./test-environment .
RUN ./dispatcher.sh -q --conf-builder=builder.conf.tools --no-run

###########################################
#         Documentation Builder          #
###########################################
FROM node:22.13-alpine AS docs-builder

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable

ARG URL_PREFIX
ARG DOCS_URL

WORKDIR /app

COPY ./bublik-release/package.json ./bublik-release/pnpm-lock.yaml ./

RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

COPY ./bublik-release .

RUN URL="${DOCS_URL}" BASE_URL="${URL_PREFIX}/docs/" pnpm run build

###########################################
#           Bublik Runner               #
###########################################
FROM base AS runner

WORKDIR /app

COPY --from=docs-builder /app/build /app/bublik/docs

COPY . ./bublik
COPY ./bublik-conf ./bublik

RUN mkdir -p bublik/logs && chmod +x ./bublik/entrypoint.sh

WORKDIR /app/bublik