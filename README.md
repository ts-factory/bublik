[SPDX-License-Identifier: Apache-2.0]::
[Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.]::

# Bublik

**Configurable Web Application to analyse TE results.**

Full documentation for the project is available at <>.

# Overview
# Requirements

- **Debian 11 or 12, or Ubuntu 22.04 or 24.04**
- **Python 3.9 or 3.10**
- **Django 3.2**

# Installation
1. Clone [bublik backend](https://github.com/ts-factory/bublik.git) to /opt/bublik/**bublik**.
2. Clone [bublik frontend](https://github.com/ts-factory/bublik-ui.git) to /opt/bublik/**bublik-ui**.
3. Clone [site-specific configuration](https://github.com/ts-factory/ts-rigs-sample.git) to /opt/bublik/**ts-rigs**.
4. Launch deploy script:

    ```
    cd /opt/bublik/bublik
    ./scripts/deploy -c ol/selftest -H bublik-db -k /etc/bublik.keytab
    ```
5. Check to NGINX settings in /etc/nginx/sites-available/bublik:
    ```
    location /v2/ {
        alias /opt/bublik/bublik-ui/dist/apps/bublik/;
        index index.html;
        try_files $uri /v2/index.html;
    }
    ```

# Examples

Demo is available at https://ts-factory.io/bublik/

# Documentation

For now some documentation can be found in **doc/wiki** here.

# Development

## Pre-commit checkings

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

## Checking your changes

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
