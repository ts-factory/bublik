FROM python:3.12 AS base

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

ENV PATH="/app/te/build/inst/default/bin:$PATH"

WORKDIR /app

RUN apt update \
  && apt install \
  gettext \
  python3-celery \
  rsync \
  flex \
  bison \
  ninja-build \
  libjansson-dev \
  libjansson-doc \
  libjansson4 \
  libpopt-dev \
  libpcre3-dev \
  pixz \
  libxml-parser-perl \
  build-essential -y && \
  cpan JSON

RUN pip install --upgrade pip && pip install meson watchfiles

RUN mkdir bublik

COPY ./requirements.txt bublik

RUN pip install -r /app/bublik/requirements.txt

WORKDIR /app/te

COPY ./test-environment .
RUN ./dispatcher.sh -q --conf-builder=builder.conf.tools --no-run

FROM node:20.3.1 AS docs-builder

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

# 2. Build bublik
FROM base AS runner

WORKDIR /app

COPY . ./bublik
COPY ./bublik-conf ./bublik
COPY --from=docs-builder /app/build ./bublik/docs

# 3. Create user and set permissions
RUN mkdir -p bublik/logs
RUN chmod +x ./bublik/entrypoint.sh

WORKDIR /app/bublik
