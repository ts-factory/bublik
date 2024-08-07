FROM python:3.10 AS base

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

# 2. Build bublik
FROM base AS runner

WORKDIR /app

COPY . ./bublik
COPY ./bublik-conf ./bublik

# 3. Create user and set permissions
RUN mkdir -p bublik/logs
RUN chmod +x ./bublik/entrypoint.sh

WORKDIR /app/bublik
