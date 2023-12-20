#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

'''
This script brings all import logs to a format close to JSON.
Logs of the new format are saved in file with the name '{task_id}' (without '.log' extension).
'''

import argparse
import json
import logging
import os
import sys

import pyparsing as pp


old_logs_extension = '.log'


def get_logdirs(av):
    '''
    Get a list of files/directories with logs from command line arguments.
    '''

    parser = argparse.ArgumentParser(
        description='Reformat the import logs from the specified directories '
        'to a format close to JSON.',
    )
    parser.add_argument(
        'logdirs',
        nargs='+',
        help='directories with import logs to be formatted',
    )
    return parser.parse_args(av[1:]).logdirs


def is_new_format_line(line):
    '''
    Check if the passed log line has a new format
    (JSON with the keys 'asctime', 'levelname', 'module', 'message').
    '''

    try:
        json_line = json.loads(line)
        return json_line.keys() == {'asctime', 'levelname', 'module', 'message'}
    except (json.decoder.JSONDecodeError, AttributeError):
        return False


def log_line_parser(line):
    '''
    Returns a new format log line corresponding to the passed line.
    Parameter @line is a string that will be converted.

    Examples are bellow.

    input: '[2022-10-26 14:57:54,479][INFO](importruns) run logs weren't processed'
    output: '{"asctime": "2022-10-26 14:57:54,479", "levelname": "INFO", '
            '"module": "importruns", "message": "run logs weren't processed"}'

    input: 'another format line'
    output: '{"asctime": "0000-00-00 00:00:00,000", "levelname": "INFO", '
            '"module": "console", "message": "another format line"}'

    Note: For a line that has an output format, the line is returned unchanged.
          For a line corresponding to the line break character, None is returned.
    '''

    if is_new_format_line(line):
        return line

    # lines consisting only of a line break character are deleted
    if line == '\n':
        return None

    asctime = (pp.Regex(r'[^\[\]]*'))('asctime')
    levelname = (pp.Regex(r'[^\[\]]*'))('levelname')
    module = (pp.Regex(r'[^\(\)]*'))('module')
    message = (pp.Regex(r'((.*)(?=\n))|(\s*)'))('message')

    log_line = (
        pp.Suppress('[')
        + asctime
        + pp.Suppress('][')
        + levelname
        + pp.Suppress('](')
        + module
        + pp.Suppress(')')
        + message
    )
    log_line = log_line.setParseAction(
        lambda t: f'{{"asctime": {json.dumps(t.asctime)}, '
        f'"levelname": {json.dumps(t.levelname)}, "module": {json.dumps(t.module)}, '
        f'"message": {json.dumps(t.message)}}}',
    )

    try:
        parsed_log_line_list = log_line.parseString(line)
    except pp.ParseException:
        line = '[0000-00-00 00:00:00,000][INFO](console) ' + line
        parsed_log_line_list = log_line.parseString(line)

    return parsed_log_line_list[0] + '\n'


def log_file_converter(logpath):
    '''
    Convert logs from the passed file into logs of a new format.
    Write the result to a file with the same name, but without extension.
    '''

    # check if the log is empty
    if os.path.getsize(logpath) == 0:
        return

    with open(logpath) as log_file:
        lines = log_file.readlines()

    parsed_log_lines = []
    for line in lines:
        parsed_log_line = log_line_parser(line)
        if parsed_log_line:
            parsed_log_lines.append(parsed_log_line)

    # new logs will not have '.log' extension in the file name
    parsed_logpath = logpath.replace(old_logs_extension, '')
    with open(parsed_logpath, 'w') as parsed_log_file:
        for line in parsed_log_lines:
            parsed_log_file.write(line)


def main(av):
    logdirs = get_logdirs(av)

    try:
        for logdir in logdirs:
            if os.path.isfile(logdir):
                logpath = logdir
                log_file_converter(logpath)
            elif os.path.isdir(logdir):
                for dirpath, _, logs in os.walk(logdir):
                    for log in logs:
                        _, extension = os.path.splitext(log)
                        # checking whether the log has the old format
                        if extension == old_logs_extension:
                            logpath = os.path.join(dirpath, log)
                            log_file_converter(logpath)
            else:
                logging.error(
                    f'The file/directory "{logdir}" was not found',
                )
                return 1
        return 0

    except pp.ParseException as pe:
        logging.error(
            f'Strange log file "{logpath}"!\n'
            f'Error message: {pe}\n'
            f'The line: "{pe.line}"',
        )
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
