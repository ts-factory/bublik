[SPDX-License-Identifier: Apache-2.0]::
[Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.]::

# Bublik

**Configurable Web Application to analyse TE results.**

## Examples

Demo is available [here](https://ts-factory.io/bublik/).

## Documentation

Full documentation is available [here](https://ts-factory.github.io/bublik-release/).

## Requirements

- Debian 12 or 13, or Ubuntu 22.04 or 24.04
- Python 3.10 or 3.11 or 3.12 or 3.13

## Installation
1. Clone [Bublik backend](https://github.com/ts-factory/bublik.git):
    ```
    git clone https://github.com/ts-factory/bublik.git
    ```
2. Launch the initial deployment script, specifying the host:
    ```
    ./scripts/init_deploy.sh <host name>
    ```
    You can also use the initial deployment and deployment options to provide additional parameters.
    To see all available options, run:
    ```
    ./scripts/init_deploy.sh -h
    ./scripts/deploy -h
    ```
3. Check to NGINX settings in /etc/nginx/sites-available/bublik:

    ```
    location /v2/ {
        alias /opt/bublik/bublik-ui/dist/bublik/;
        index index.html;
        try_files $uri /v2/index.html;
    }
    ```

## Development

### Pre-commit checkings

After initial deploy please run the:
```
pre-commit install
```
This will allow the pre-commit tool to run `./scripts/pyformat -c` before each
commit.

Please note that you can always disable the pre-commit validation by running:
```
pre-commit uninstall
```

### Checking your changes

You can use pyformat script to check your changes.

For this you need to run:
```
./scripts/pyformat -c <path_to_the_changes_file>
```

Then you can apply changes, if any, by running:
```
./scripts/pyformat <path_to_the_changed_file>
```

For more information, you can refer to the scripts/pyformat help section.
