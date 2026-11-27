#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

"""
Copy a run from one Bublik instance to another.

Bublik imports a run by fetching its logs from a URL, and only from URLs under
the logs bases (LOGS_BASES) of its references configuration. A run imported
from logs that the target instance can't reach (e.g. logs on a local machine)
is copied by publishing its logs to a location the target instance imports
from and importing them there:

    migrate_run.py migrate <run URL> --to <target Bublik URL> \\
        [--dest <rsync destination> --dest-url <URL it is served at>] \\
        [--project <target project name or ID>] [--wait]

The run is imported into the given project, or into the one named in
meta_data.json of its logs, as Bublik does. The command returns once the import
is scheduled, unless --wait is given; the import is then waited for and checked
with the command given in the result:

    migrate_run.py wait --to <target Bublik URL> --task <import task ID> \\
        --source <run URL>

which waits for no more than --timeout seconds (default 90) at a time, so that
each command finishes within the time limit of an agent's shell.

Logs alone can also be published:

    migrate_run.py publish <logs URL or directory> \\
        --dest <rsync destination> --dest-url <URL it is served at>

Logs are checked before being published as the import checks them: meta_data.json
and the run log (converted to JSON as the import does) against their schemas.
Dropped connections and timeouts, of HTTP requests and of rsync over SSH, are
retried a couple of times.

Progress is written to stderr, the result as a single JSON object to stdout. Its
"status" tells the outcome and "summary" describes it in a sentence. Unless the
outcome is final, "next" tells what to do: a command to run ("run"), or
a question to ask and the command to run with the answer ("ask", "then_run").

Exit codes:
    0   the command did what it could, see "status"
    1   failed, see "error"
    2   invalid arguments
"""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

import jsonschema
import requests


try:
    from requests_kerberos import DISABLED, HTTPKerberosAuth
except ImportError:  # Kerberos is only needed for servers that require it
    HTTPKerberosAuth = None


# Files Bublik looks for in a run logs directory: meta_data.json and the run
# logs in the order the import tries them (see bublik.core.importruns.import_run)
RUN_LOG_FILES = (
    'bublik.json',
    'bublik.xml',
    'log.json.xz',
    'log.xml.xz',
    'raw_log_bundle.tpxz',
)
RUN_FILES = ('meta_data.json', *RUN_LOG_FILES)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMAS_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), 'bublik', 'data', 'schemas')
XML_LOG_PARSER = os.path.join(SCRIPTS_DIR, 'xml_log_parser')

EXIT_OK = 0
EXIT_ERROR = 1

FINAL_STATUSES = ('SUCCESS', 'FAILURE', 'REVOKED')
POLL_INTERVAL = 5
MIGRATE_WAIT_TIMEOUT = 1800
WAIT_TIMEOUT = 90
HTTP_TIMEOUT = 60
RETRIES = 2
RETRY_PAUSE = 10
# rsync exit code meaning that the remote shell (SSH) connection failed
RSYNC_REMOTE_SHELL_ERROR = 255
CHUNK_SIZE = 1 << 16
MAX_MIRROR_DEPTH = 32


class MigrationError(Exception):
    def __init__(self, message, step):
        super().__init__(message)
        self.step = step
        self.message = message


class ProjectRequiredError(MigrationError):
    def __init__(self, message, projects):
        super().__init__(message, 'project')
        self.choices = sorted(project['name'] for project in projects)


class DestRequiredError(MigrationError):
    def __init__(self, logs_uris):
        message = 'the logs have to be published, but where to is not given'
        super().__init__(message, 'publish')
        self.logs_uris = logs_uris


def progress(step, message):
    sys.stderr.write(f'[{step}] {message}\n')
    sys.stderr.flush()


def with_slash(url):
    return url if url.endswith('/') else url + '/'


class Http:
    """HTTP client authenticating with Kerberos when a server requires it, as Bublik does."""

    def __init__(self):
        self.session = requests.Session()
        if HTTPKerberosAuth is not None:
            self.session.auth = HTTPKerberosAuth(mutual_authentication=DISABLED)

    def request(self, step, method, url, allow_404=False, consume=None, retry=True, **kwargs):
        """
        Make a request and return the response, or what consume() returns for
        it if given, e.g. after reading a streamed response.

        A request failing to get a response, or a streamed response failing to
        be read, is retried on a new connection, unless retry is False, as for
        a request that must not be repeated if it reached the server.
        """
        kwargs.setdefault('timeout', HTTP_TIMEOUT)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._request(step, method, url, allow_404, consume, **kwargs)
            except requests.RequestException as e:
                # A connection that was never established surely sent nothing
                can_retry = retry or isinstance(e, requests.ConnectTimeout)
                if not can_retry or attempt > RETRIES:
                    msg = f'{method} {url} failed: {e}'
                    raise MigrationError(msg, step) from None
                progress(step, f'{method} {url} failed: {e}; retrying in {RETRY_PAUSE} s')
                self.session.close()  # drop connections that may be broken
                time.sleep(RETRY_PAUSE)

    def _request(self, step, method, url, allow_404, consume, **kwargs):
        response = self.session.request(method, url, **kwargs)
        # Bublik also retries once: CGI-generated logs files answer 302,
        # and requests doesn't authenticate on the redirect
        if response.status_code == requests.codes.unauthorized:
            response = self.session.request(method, url, **kwargs)

        if allow_404 and response.status_code == requests.codes.not_found:
            return None
        if not response.ok:
            body = response.text.strip()[:500]
            msg = f'{method} {url} returned {response.status_code}: {body}'
            raise MigrationError(msg, step)
        return consume(response) if consume is not None else response

    def get(self, step, url, allow_404=False, **kwargs):
        return self.request(step, 'GET', url, allow_404=allow_404, **kwargs)

    def get_json(self, step, url, allow_404=False, **kwargs):
        response = self.get(step, url, allow_404=allow_404, **kwargs)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError:
            msg = f'GET {url} returned no JSON: {response.text.strip()[:200]}'
            raise MigrationError(msg, step) from None


# Bublik instances


def bublik_base(url):
    """Return the root URL of a Bublik instance given any URL of its UI or API."""
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        msg = f'not an HTTP(S) URL: {url}'
        raise MigrationError(msg, 'arguments')
    match = re.search(r'/(?:api/)?v2(?:/|$)', parts.path)
    path = parts.path[: match.start() + 1] if match else with_slash(parts.path)
    return f'{parts.scheme}://{parts.netloc}{path}'


def parse_run_url(url):
    """Return the root URL of the Bublik instance and the run ID given a run URL."""
    base = bublik_base(url)
    match = re.search(r'/(?:api/)?v2/(?:runs|log)/(\d+)', urlsplit(url).path)
    if not match:
        msg = f'not a URL of a run in Bublik (expected .../v2/runs/<run ID>): {url}'
        raise MigrationError(msg, 'arguments')
    return base, int(match.group(1))


def run_ui_url(base, run_id):
    return f'{base}v2/runs/{run_id}'


def get_source_run(http, base, run_id):
    step = 'source'
    details = http.get_json(step, f'{base}api/v2/runs/{run_id}/details/', allow_404=True)
    if details is None:
        msg = f'run {run_id} not found in {base}'
        raise MigrationError(msg, step)
    if details.get('finish') is None:
        msg = f'run {run_id} is not finished, copy it when it is'
        raise MigrationError(msg, step)

    logs_url = http.get_json(step, f'{base}api/v2/runs/{run_id}/source/').get('url')
    if not logs_url:
        msg = f'run {run_id} has no logs URL, so there is nothing to import from'
        raise MigrationError(msg, step)

    return {
        'bublik': base,
        'run_id': run_id,
        'run_url': run_ui_url(base, run_id),
        'project': details.get('project_name'),
        'logs_url': with_slash(logs_url),
    }


def get_projects(http, base):
    return http.get_json('target', f'{base}api/v2/projects/')


def find_project(projects, value, what):
    """Return the project of the target instance given its name or ID."""
    for key in ('name', 'id'):
        for project in projects:
            if str(project[key]) == value:
                return project
    msg = f'{what} does not exist in the target instance'
    raise ProjectRequiredError(msg, projects)


def read_meta_data_project(http, logs_url=None, logs_dir=None):
    """
    Return whether the logs have meta_data.json and the value of its PROJECT
    meta, which the import takes the project from when none is given.
    """
    if logs_dir is not None:
        path = os.path.join(logs_dir, 'meta_data.json')
        if not os.path.isfile(path):
            return False, None
        with open(path) as f:
            content = f.read()
    else:
        response = http.get('project', logs_url + 'meta_data.json', allow_404=True)
        if response is None:
            return False, None
        content = response.text

    try:
        metas = json.loads(content).get('metas') or []
    except (ValueError, AttributeError):
        return True, None  # the import reports what is wrong with it
    for meta in metas:
        if isinstance(meta, dict) and meta.get('name') == 'PROJECT':
            return True, meta.get('value')
    return True, None


def choose_project(http, report, projects, project_arg, logs_url=None, logs_dir=None):
    """
    Return the ID of the target instance project to import into: the given one,
    or the one named in meta_data.json of the logs, as the import does.
    """
    step = 'project'
    has_meta_data, meta_data_project = read_meta_data_project(http, logs_url, logs_dir)
    info = report['project']
    info['meta_data_project'] = meta_data_project

    if project_arg is not None:
        project = find_project(projects, project_arg, f'project "{project_arg}"')
        info['chosen_by'] = '--project'
    elif not has_meta_data:
        msg = 'the logs have no meta_data.json to take the project from'
        raise ProjectRequiredError(msg, projects)
    elif not meta_data_project:
        msg = 'meta_data.json of the logs names no project (PROJECT meta)'
        raise ProjectRequiredError(msg, projects)
    else:
        what = f'project "{meta_data_project}" named in meta_data.json'
        project = find_project(projects, meta_data_project, what)
        info['chosen_by'] = 'meta_data.json'

    info['target'] = {'id': project['id'], 'name': project['name']}
    progress(step, f'importing into project "{project["name"]}" (by {info["chosen_by"]})')
    return project['id']


def get_logs_uris(http, base, project_ids):
    """
    Return URIs under which the Bublik instance imports logs into the projects,
    as the import checks (see ImportrunsSerializer.validate_url).
    """
    step = 'target'
    uris = set()
    for project_id in project_ids:
        data = http.get_json(
            step,
            f'{base}api/v2/outside_domains/logs/',
            params={'project': project_id},
        )
        for logs_base in data.get('logs') or []:
            uris.update(logs_base.get('uri', []))
    return sorted(uris)


def is_importable(url, logs_uris):
    return any(url.startswith(uri) for uri in logs_uris)


# Publishing logs


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.hrefs.append(href)


def _listing_entries(html):
    """
    Yield (name, href, is_dir) for entries of a directory listing page, skipping
    links leading outside the directory (parent, absolute, sorting links).
    """
    parser = _LinkParser()
    parser.feed(html)
    seen = set()
    for raw_href in parser.hrefs:
        href = raw_href.split('#', 1)[0].split('?', 1)[0]
        if href.startswith('./'):
            href = href[2:]
        if not href or href.startswith('/') or '://' in href or ':' in href.split('/')[0]:
            continue
        is_dir = href.endswith('/')
        name = unquote(href[:-1] if is_dir else href)
        if not name or '/' in name or name in ('.', '..') or href in seen:
            continue
        seen.add(href)
        yield name, href, is_dir


def _download(http, url, path):
    def save(response):
        with open(path, 'wb') as f:
            for chunk in response.iter_content(CHUNK_SIZE):
                f.write(chunk)

    http.get('publish', url, stream=True, consume=save)


def mirror(http, url, directory, depth=0):
    """Download the logs directory at the URL, relying on its listing, as Bublik does."""
    if depth > MAX_MIRROR_DEPTH:
        msg = f'the logs directory is nested deeper than {MAX_MIRROR_DEPTH} levels: {url}'
        raise MigrationError(msg, 'publish')

    response = http.get('publish', url, allow_404=True)
    if response is None or 'html' not in response.headers.get('Content-Type', ''):
        msg = (
            f'no listing of the logs directory {url}, so its files are unknown; '
            'pass the directory with the logs as --logs-dir'
        )
        raise MigrationError(msg, 'publish')

    for name, href, is_dir in _listing_entries(response.text):
        path = os.path.join(directory, name)
        if is_dir:
            os.makedirs(path, exist_ok=True)
            mirror(http, urljoin(url, href), path, depth + 1)
        else:
            _download(http, urljoin(url, href), path)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_url(http, url):
    def sha256(response):
        digest = hashlib.sha256()
        for chunk in response.iter_content(CHUNK_SIZE):
            digest.update(chunk)
        return digest.hexdigest()

    return http.get('publish', url, allow_404=True, stream=True, consume=sha256)


def find_run_file(http, logs_url=None, logs_dir=None):
    """
    Return the name and SHA-256 of the first of RUN_FILES in the logs, which
    identifies the logs and tells that they are logs of a run.
    """
    for name in RUN_FILES:
        if logs_dir is not None:
            path = os.path.join(logs_dir, name)
            if os.path.isfile(path):
                return name, sha256_file(path)
        else:
            digest = sha256_url(http, logs_url + name)
            if digest is not None:
                return name, digest
    where = logs_dir if logs_dir is not None else logs_url
    msg = f'no run logs in {where}: none of {", ".join(RUN_FILES)} found'
    raise MigrationError(msg, 'publish')


def publish_name(logs_url):
    """
    Return the path to publish logs at relative to the destination: the host and
    path of their URL, so that the same logs are always published at one place.
    """
    parts = urlsplit(logs_url)
    segments = [parts.netloc.replace(':', '_')]
    segments += [unquote(segment) for segment in parts.path.split('/')]
    segments = [re.sub(r'[^\w.-]', '_', s) for s in segments if s not in ('', '.', '..')]
    return '/'.join(segments)


def rsync(source_dir, destination):
    if shutil.which('rsync') is None:
        msg = 'rsync is not installed'
        raise MigrationError(msg, 'publish')
    command = [
        'rsync',
        '--archive',
        '--mkpath',
        '--chmod=D755,F644',
        with_slash(source_dir),
        destination,
    ]
    attempt = 0
    while True:
        attempt += 1
        progress('publish', ' '.join(command))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return
        error = result.stderr.strip()
        if result.returncode != RSYNC_REMOTE_SHELL_ERROR or attempt > RETRIES:
            msg = f'rsync failed with code {result.returncode}: {error[-500:]}'
            raise MigrationError(msg, 'publish')
        reason = error.splitlines()[0] if error else 'no error message'
        progress('publish', f'rsync failed to connect: {reason}; retrying in {RETRY_PAUSE} s')
        time.sleep(RETRY_PAUSE)


def validate_json(instance, schema_name, what):
    with open(os.path.join(SCHEMAS_DIR, f'{schema_name}.json')) as f:
        schema = json.load(f)
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.exceptions.ValidationError as e:
        where = '/'.join(str(item) for item in e.absolute_path) or 'the root'
        msg = f'invalid {what} format: {e.message} (at {where})'
        raise MigrationError(msg, 'validate') from None


def load_json(path, what):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        msg = f'invalid {what} format: {e}'
        raise MigrationError(msg, 'validate') from None


def run_converter(command, stdout_path=None):
    """Run a command converting a log, as the import does (see bublik.core.importruns.telog)."""
    stdout = open(stdout_path, 'wb') if stdout_path else subprocess.DEVNULL  # noqa: SIM115
    try:
        result = subprocess.run(command, stdout=stdout, stderr=subprocess.PIPE, check=False)
    except OSError as e:
        msg = f'cannot check the run log as the import does: cannot run {command[0]}: {e}'
        raise MigrationError(msg, 'validate') from None
    finally:
        if stdout_path:
            stdout.close()
    if result.returncode != 0:
        error = result.stderr.decode(errors='replace').strip()[-500:]
        msg = (
            f'cannot convert the run log as the import does: {" ".join(command)} '
            f'failed with code {result.returncode}: {error}'
        )
        raise MigrationError(msg, 'validate')


def convert_run_log(logs_dir, log_file, work_dir):
    """Return the run log converted to JSON as the import does (JSONLog.convert_from_dir)."""
    path = os.path.join(logs_dir, log_file)
    json_path = os.path.join(work_dir, 'log.json')
    xml_path = path

    if log_file == 'bublik.json':
        return load_json(path, 'run log')
    if log_file == 'log.json.xz':
        run_converter(['xz', '-d', '-k', '-c', path], json_path)
        return load_json(json_path, 'run log')
    if log_file == 'log.xml.xz':
        xml_path = os.path.join(work_dir, 'log.xml')
        run_converter(['xz', '-d', '-k', '-c', path], xml_path)
    elif log_file == 'raw_log_bundle.tpxz':
        raw_path = os.path.join(work_dir, 'tmp_raw_log')
        xml_path = os.path.join(work_dir, 'log.xml')
        run_converter(
            ['rgt-log-bundle-get-original', f'--bundle={path}', f'--raw-log={raw_path}'],
        )
        run_converter(['te-trc-log', '--mi-meta', raw_path, xml_path])
    run_converter([XML_LOG_PARSER, xml_path], json_path)
    return load_json(json_path, 'run log')


def validate_run_logs(logs_dir):
    """
    Check the logs as the import does: meta_data.json and the run log converted
    to JSON, if any, against their schemas.
    """
    step = 'validate'
    checked = {}
    meta_data_path = os.path.join(logs_dir, 'meta_data.json')
    if os.path.isfile(meta_data_path):
        meta_data = load_json(meta_data_path, 'meta_data.json')
        validate_json(meta_data, 'meta_data', 'meta_data.json')
        checked['meta_data.json'] = 'valid'
    else:
        checked['meta_data.json'] = 'absent'

    log_file = next(
        (name for name in RUN_LOG_FILES if os.path.isfile(os.path.join(logs_dir, name))),
        None,
    )
    if log_file is None:
        # The import goes on without a run log, adding the run with its metas only
        progress(
            step,
            f'warning: no run log ({", ".join(RUN_LOG_FILES)}), '
            'the run will be imported with its metas only',
        )
        checked['run log'] = 'absent'
        return checked

    progress(step, f'checking {log_file}')
    with tempfile.TemporaryDirectory(prefix='bublik-migrate-check-') as work_dir:
        run_log = convert_run_log(logs_dir, log_file, work_dir)
    validate_json(run_log, 'run_log', 'run log')
    checked[log_file] = 'valid'
    progress(step, 'the logs are valid')
    return checked


def publish(http, report, dest, dest_url, name, logs_url=None, logs_dir=None, logs_uris=None):
    """
    Publish logs given by URL or directory to dest/name/, served at
    dest_url/name/, and return the URL they are published at.
    """
    step = 'publish'
    if not dest or not dest_url:
        raise DestRequiredError(logs_uris)

    published_url = urljoin(with_slash(dest_url), quote(name) + '/')
    destination = with_slash(dest.rstrip('/') + '/' + name)
    info = report['publish'] = {
        'from': logs_dir if logs_dir is not None else logs_url,
        'to': destination,
        'url': published_url,
    }
    if logs_uris is not None and not is_importable(published_url, logs_uris):
        msg = (
            f'the target instance does not import from {published_url}: '
            f'--dest-url must be under one of its logs bases {logs_uris}'
        )
        raise MigrationError(msg, step)

    run_file, digest = find_run_file(http, logs_url=logs_url, logs_dir=logs_dir)
    published_digest = sha256_url(http, published_url + run_file)
    if published_digest == digest:
        progress(step, f'already published at {published_url}')
        info['status'] = 'already_published'
        return published_url
    if published_digest is not None:
        msg = (
            f'{published_url}{run_file} already exists and differs from '
            'the one being published, not overwriting it'
        )
        raise MigrationError(msg, step)

    with tempfile.TemporaryDirectory(prefix='bublik-migrate-') as tmp:
        if logs_dir is None:
            progress(step, f'downloading {logs_url}')
            mirror(http, logs_url, tmp)
            source_dir = tmp
        else:
            source_dir = logs_dir
        info['checked'] = validate_run_logs(source_dir)

        if report.get('dry_run'):
            info['status'] = 'would_publish'
            return published_url
        rsync(source_dir, destination)

    if sha256_url(http, published_url + run_file) != digest:
        msg = (
            f'the logs were copied to {destination}, but {published_url}{run_file} '
            'is not served or differs: check that --dest-url is the URL --dest is served at'
        )
        raise MigrationError(msg, step)

    progress(step, f'published at {published_url}')
    info['status'] = 'published'
    return published_url


# Importing


def schedule_import(http, base, url, project_id):
    step = 'import'
    params = {'url': url}
    if project_id is not None:
        params['project'] = project_id
    # Not retried: a request that reached Bublik may have scheduled the import
    data = http.get_json(
        step,
        f'{base}api/v2/importruns/source/',
        params=params,
        retry=False,
    )
    tasks = data.get('job_tasks_data') or []
    task_ids = [task['celery_task_id'] for task in tasks if task.get('celery_task_id')]
    if len(tasks) != 1 or len(task_ids) != 1:
        msg = f'expected the import of one run at {url}, Bublik scheduled: {tasks}'
        raise MigrationError(msg, step)
    return data.get('job_id'), task_ids[0]


def wait_for_import(http, base, task_id, timeout):
    """
    Return the import task once it is finished, or None if it isn't in
    timeout seconds. Fail if the task is not found in that time.
    """
    step = 'import'
    deadline = time.monotonic() + timeout
    status = None
    while True:
        data = http.get_json(
            step,
            f'{base}api/v2/session_import/',
            params={'celery_task': task_id},
            allow_404=True,  # until the task execution is recorded
        )
        rows = (data.get('results') if isinstance(data, dict) else data) or []
        if rows:
            task = rows[0]
            if task['status'] != status:
                status = task['status']
                progress(step, f'task {task_id}: {status}')
            if status in FINAL_STATUSES:
                return task
        if time.monotonic() >= deadline:
            if status is None:
                msg = f'import task {task_id} not found in {base}'
                raise MigrationError(msg, step)
            return None
        time.sleep(POLL_INTERVAL)


def get_run_stats(http, base, run_id):
    data = http.get_json('verify', f'{base}api/v2/runs/{run_id}/stats/')
    root = data.get('results')
    return root.get('stats') if root else None


# Commands to go on with


def script_command(*arguments):
    return shlex.join(['python3', sys.argv[0], *arguments])


def rerun_command(args, without=()):
    """Return the command line of this command, without the given options."""
    if args.command == 'migrate':
        arguments = ['migrate', args.run_url, '--to', args.to]
        options = [('--project', args.project), ('--logs-dir', args.logs_dir)]
    else:
        arguments = ['publish', args.logs]
        options = [('--name', args.name), ('--to', args.to), ('--project', args.project)]
    options += [('--dest', args.dest), ('--dest-url', args.dest_url)]
    for option, value in options:
        if value is not None and option not in without:
            arguments += [option, value]
    return script_command(*arguments)


def wait_command(target, task_id, source_run_url):
    return script_command('wait', '--to', target, '--task', task_id, '--source', source_run_url)


def finish_import(http, report, target, task_id, source_base, run_id, timeout):
    """Wait for the import and report its outcome, checking the new run."""
    info = report.setdefault('import', {})
    info['task_id'] = task_id
    info['import_log'] = f'{target}api/v2/importruns/log/?task_id={task_id}'
    target_info = report.setdefault('target', {'bublik': target})

    task = wait_for_import(http, target, task_id, timeout)
    if task is None:
        info['status'] = 'RUNNING'
        report['status'] = 'import_running'
        report['summary'] = 'The import is still running.'
        report['next'] = {'run': wait_command(target, task_id, run_ui_url(source_base, run_id))}
        return

    info['status'] = task['status']
    target_run_id = task.get('run_id')
    error_msg = task.get('error_msg') or ''

    if task['status'] == 'FAILURE' and target_run_id and 'run already exists' in error_msg:
        target_url = run_ui_url(target, target_run_id)
        target_info.update(run_id=target_run_id, run_url=target_url)
        report['status'] = 'skipped'
        report['summary'] = (
            f'The run already exists in the target instance: {target_url}. Nothing is imported.'
        )
        progress('import', f'skipped: the run already exists as {target_run_id}')
        return

    if task['status'] != 'SUCCESS' or not target_run_id:
        msg = f'the import ended with {task["status"]}: {error_msg or "no error message"}'
        raise MigrationError(msg, 'import')

    target_url = run_ui_url(target, target_run_id)
    target_info.update(run_id=target_run_id, run_url=target_url)
    progress('import', f'imported as run {target_run_id}')

    source_stats = get_run_stats(http, source_base, run_id)
    target_stats = get_run_stats(http, target, target_run_id)
    matches = source_stats is not None and source_stats == target_stats
    report['verification'] = {
        'stats_match': matches,
        'source_stats': source_stats,
        'target_stats': target_stats,
    }
    if matches:
        report['status'] = 'migrated'
        report['summary'] = f'The run is copied to {target_url}, its statistics match.'
        progress('verify', 'the run statistics match')
        return

    source_stats = source_stats or {}
    target_stats = target_stats or {}
    differences = ', '.join(
        f'{key} {source_stats.get(key)} -> {target_stats.get(key)}'
        for key in sorted(set(source_stats) | set(target_stats))
        if source_stats.get(key) != target_stats.get(key)
    )
    report['status'] = 'migrated_stats_differ'
    report['summary'] = (
        f'The run is copied to {target_url}, but its statistics differ from those of '
        f'the source run: {differences or "no statistics"}.'
    )
    progress('verify', 'the run statistics differ from those of the source run')


# Commands


def cmd_publish(args, http, report):
    report['command'] = 'publish'
    logs_dir = logs_url = None
    if re.match(r'https?://', args.logs):
        logs_url = with_slash(args.logs)
        name = args.name or publish_name(logs_url)
    else:
        logs_dir = os.path.abspath(args.logs)
        if not os.path.isdir(logs_dir):
            msg = f'not a directory: {args.logs}'
            raise MigrationError(msg, 'arguments')
        name = args.name or os.path.basename(logs_dir)

    logs_uris = None
    if args.to:
        target = bublik_base(args.to)
        projects = get_projects(http, target)
        if args.project is not None:
            projects = [find_project(projects, args.project, f'project "{args.project}"')]
        logs_uris = get_logs_uris(http, target, [project['id'] for project in projects])
        report['target'] = {'bublik': target, 'logs_uris': logs_uris}

    url = publish(
        http,
        report,
        args.dest,
        args.dest_url,
        name,
        logs_url=logs_url,
        logs_dir=logs_dir,
        logs_uris=logs_uris,
    )
    if args.dry_run:
        report['status'] = 'dry_run'
        report['summary'] = f'Dry run: the logs are valid and would be published at {url}.'
        if report['publish']['status'] == 'already_published':
            report['summary'] = f'Dry run: the logs are already published at {url}.'
        else:
            report['next'] = {'run': rerun_command(args)}
    else:
        report['status'] = 'published'
        report['summary'] = f'The logs are published at {url}.'
    return EXIT_OK


def cmd_migrate(args, http, report):
    report['command'] = 'migrate'
    source_base, run_id = parse_run_url(args.run_url)
    target = bublik_base(args.to)
    if args.logs_dir is not None and not os.path.isdir(args.logs_dir):
        msg = f'not a directory: {args.logs_dir}'
        raise MigrationError(msg, 'arguments')

    progress('source', f'run {run_id} in {source_base}')
    source = report['source'] = get_source_run(http, source_base, run_id)
    progress('source', f'logs: {source["logs_url"]}')
    logs_dir = os.path.abspath(args.logs_dir) if args.logs_dir else None

    report['project'] = {'source': source['project']}
    project_id = choose_project(
        http,
        report,
        get_projects(http, target),
        args.project,
        logs_url=source['logs_url'],
        logs_dir=logs_dir,
    )
    logs_uris = get_logs_uris(http, target, [project_id])
    report['target'] = {'bublik': target, 'logs_uris': logs_uris}

    if is_importable(source['logs_url'], logs_uris):
        progress('publish', 'the target instance imports from the logs URL, not publishing')
        import_url = source['logs_url']
        report['publish'] = {'status': 'not_needed'}
    else:
        progress('publish', 'the target instance does not import from the logs URL')
        import_url = publish(
            http,
            report,
            args.dest,
            args.dest_url,
            publish_name(source['logs_url']),
            logs_url=source['logs_url'],
            logs_dir=logs_dir,
            logs_uris=logs_uris,
        )

    info = report['import'] = {'url': import_url}
    project_name = report['project']['target']['name']
    if args.dry_run:
        report['status'] = 'dry_run'
        report['summary'] = (
            f'Dry run: the run would be imported from {import_url} '
            f'into project "{project_name}" of {target}.'
        )
        report['next'] = {'run': rerun_command(args)}
        return EXIT_OK

    progress('import', f'importing {import_url} into {target}')
    info['job_id'], task_id = schedule_import(http, target, import_url, project_id)
    info['task_id'] = task_id
    if args.wait:
        finish_import(http, report, target, task_id, source_base, run_id, args.timeout)
        return EXIT_OK

    info['status'] = 'RECEIVED'
    info['import_log'] = f'{target}api/v2/importruns/log/?task_id={task_id}'
    report['status'] = 'import_scheduled'
    report['summary'] = f'The import into project "{project_name}" of {target} is scheduled.'
    report['next'] = {'run': wait_command(target, task_id, source['run_url'])}
    return EXIT_OK


def cmd_wait(args, http, report):
    report['command'] = 'wait'
    source_base, run_id = parse_run_url(args.source)
    target = bublik_base(args.to)
    report['source'] = {
        'bublik': source_base,
        'run_id': run_id,
        'run_url': run_ui_url(source_base, run_id),
    }
    report['target'] = {'bublik': target}
    finish_import(http, report, target, args.task, source_base, run_id, args.timeout)
    return EXIT_OK


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Copy a run from one Bublik instance to another, '
        'publishing its logs where the target instance imports from if needed.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    def add_dest_args(subparser):
        subparser.add_argument(
            '--dest',
            default=os.environ.get('BUBLIK_MIGRATE_DEST'),
            help='rsync destination to publish logs to, e.g. user@logs:/srv/logs/migrated/ '
            '(default: $BUBLIK_MIGRATE_DEST)',
        )
        subparser.add_argument(
            '--dest-url',
            default=os.environ.get('BUBLIK_MIGRATE_DEST_URL'),
            help='URL --dest is served at, e.g. https://logs.example.com/migrated/ '
            '(default: $BUBLIK_MIGRATE_DEST_URL)',
        )
        subparser.add_argument(
            '--dry-run',
            action='store_true',
            help='only check and report what would be done',
        )

    migrate = subparsers.add_parser('migrate', help='copy a run to another instance')
    migrate.add_argument('run_url', help='URL of the run in the source Bublik instance')
    migrate.add_argument('--to', required=True, help='URL of the target Bublik instance')
    migrate.add_argument(
        '--logs-dir',
        help='directory with the run logs, to publish instead of downloading them',
    )
    migrate.add_argument(
        '--project',
        help='name or ID of the project in the target instance to import into '
        '(default: the project named in meta_data.json of the logs)',
    )
    migrate.add_argument(
        '--wait',
        action='store_true',
        help='wait for the import to finish and check it, instead of returning once '
        'it is scheduled',
    )
    migrate.add_argument(
        '--timeout',
        type=int,
        default=MIGRATE_WAIT_TIMEOUT,
        help=f'with --wait, seconds to wait for the import (default: {MIGRATE_WAIT_TIMEOUT})',
    )
    add_dest_args(migrate)

    wait = subparsers.add_parser('wait', help='wait for an import scheduled by migrate')
    wait.add_argument('--to', required=True, help='URL of the target Bublik instance')
    wait.add_argument('--task', required=True, help='ID of the import task')
    wait.add_argument('--source', required=True, help='URL of the run in the source instance')
    wait.add_argument(
        '--timeout',
        type=int,
        default=WAIT_TIMEOUT,
        help=f'seconds to wait for the import (default: {WAIT_TIMEOUT})',
    )

    publish_parser = subparsers.add_parser('publish', help='only publish run logs')
    publish_parser.add_argument('logs', help='URL or directory of the run logs')
    publish_parser.add_argument(
        '--name',
        help='path to publish the logs at relative to --dest (default: host and path '
        'of the logs URL, or the directory name)',
    )
    publish_parser.add_argument(
        '--to',
        help='URL of a Bublik instance to check that it will import from --dest-url',
    )
    publish_parser.add_argument(
        '--project',
        help='name or ID of the project in the --to instance to check the logs bases '
        'of (default: all projects)',
    )
    add_dest_args(publish_parser)

    return parser.parse_args(argv)


def project_question(error, report):
    question = f'{error.message[0].upper()}{error.message[1:]}.'
    source_project = report.get('project', {}).get('source')
    if source_project:
        question += f' In the source instance, the run is in project "{source_project}".'
    return f'{question} Which project should it be imported into: {", ".join(error.choices)}?'


def dest_question(error):
    question = (
        'The logs have to be published where the target instance imports them from. '
        'Where to publish them: an rsync destination (a directory or host:/path/) '
        'and the URL it is served at'
    )
    if error.logs_uris:
        question += f', under one of {", ".join(error.logs_uris)}'
    return question + '?'


def main(argv=None):
    args = parse_args(argv)
    http = Http()
    report = {'status': None, 'dry_run': getattr(args, 'dry_run', False)}
    commands = {'migrate': cmd_migrate, 'wait': cmd_wait, 'publish': cmd_publish}
    try:
        code = commands[args.command](args, http, report)
    except ProjectRequiredError as e:
        report['status'] = 'project_required'
        report.setdefault('project', {})['choices'] = e.choices
        question = project_question(e, report)
        report['summary'] = question
        report['next'] = {
            'ask': question,
            'then_run': rerun_command(args, without=('--project',)) + ' --project <PROJECT>',
        }
        progress(e.step, f'{e.message}; choose one of {", ".join(e.choices)} as --project')
        code = EXIT_OK
    except DestRequiredError as e:
        report['status'] = 'dest_required'
        question = dest_question(e)
        report['summary'] = question
        report['next'] = {
            'ask': question,
            'then_run': rerun_command(args, without=('--dest', '--dest-url'))
            + ' --dest <DESTINATION> --dest-url <URL>',
        }
        progress(e.step, f'{e.message}: pass --dest and --dest-url')
        code = EXIT_OK
    except MigrationError as e:
        report['status'] = 'error'
        report['error'] = {'step': e.step, 'message': e.message}
        report['summary'] = f'Failed at the {e.step} step: {e.message}'
        progress(e.step, f'error: {e.message}')
        code = EXIT_ERROR
    except KeyboardInterrupt:
        report['status'] = 'error'
        report['error'] = {'step': 'interrupted', 'message': 'interrupted'}
        report['summary'] = 'Interrupted.'
        code = EXIT_ERROR

    sys.stdout.write(json.dumps(report, indent=2) + '\n')
    return code


if __name__ == '__main__':
    sys.exit(main())
