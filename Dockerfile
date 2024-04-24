# 1. Prepare base image with all dependencies
FROM python:3.9 as base

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

COPY ./bublik/requirements.txt bublik

RUN pip install -r /app/bublik/requirements.txt

WORKDIR /app/te

COPY ../test-environment .
RUN ./dispatcher.sh -q --conf-builder=builder.conf.tools --no-run

# 2. Build bublik
FROM base as runner

WORKDIR /app

COPY ../bublik bublik
COPY ../bublik-conf bublik-conf

# 3. Create user and set permissions
RUN mkdir -p bublik/logs
RUN groupadd -r bublik-user && useradd -r -g bublik-user bublik-user
RUN chmod +x bublik/entrypoint.sh
RUN chown -R bublik-user:bublik-user .

WORKDIR /app/bublik

USER bublik-user