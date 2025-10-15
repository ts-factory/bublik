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

    > **Important:** Make sure you have root (or `sudo`) access to the target host
    > before running the initial deployment script.

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

Pre-commit installs a local Git hook that runs before `git commit` creates a
commit. In this project, the hook runs Ruff on changed Python files via:
```
./scripts/pyformat -c <changed-python-files>
```

Install the hook once after setting up the repository:
```
pre-commit install
```

After that, every `git commit` will automatically run:
```
ruff format --check --diff <changed-python-files>
ruff check <changed-python-files>
```

If the hook fails, fix the reported issues and run `git commit` again. To apply
Ruff formatting and autofixes manually, run:
```
./scripts/pyformat <path_to_the_changed_file>
```

You can run the hook manually for all files with:
```
pre-commit run --all-files
```

You can disable the local hook with:
```
pre-commit uninstall
```

### Checking your changes

You can use pyformat script to run Ruff formatting and lint checks.

For this you need to run:
```
./scripts/pyformat -c <path_to_the_changes_file>
```

Then you can apply changes, if any, by running:
```
./scripts/pyformat <path_to_the_changed_file>
```

For more information, you can refer to the scripts/pyformat help section.
