#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
'''
This script generates a secret key for Django application.

The Django secret key is used to provide cryptographic signing and makes use of this in various
higher-level features:
    - Signing serialised data (e.g. JSON documents).
    - Unique tokens for a user session, password reset request, messages, etc.
    - Prevention of cross-site or replay attacks by adding (and then expecting) unique values
    for the request.
    - Generating a unique salt for hash functions.

To revoke the key, a new secret needs to be generated. All sessions or cookies signed with the
key will be invalided.
'''

import argparse
import logging
import os
import random
import string
import sys


def main(av):
    parser = argparse.ArgumentParser(description='Secret key generator')
    parser.add_argument(
        '-f',
        '--force',
        action='store_true',
        help='Overwrite any existing secret key file',
    )
    parser.add_argument(
        '-n',
        '--length',
        type=int,
        default=50,
        choices=range(16, 128),
        metavar='[16-128]',
        help='Length of the secret key to generate',
    )
    parser.add_argument(
        '-p',
        '--path',
        default='secret.txt',
        help='Path to the secret key file',
    )
    opt = parser.parse_args(av[1:])

    if os.path.isfile(opt.path) and not opt.force:
        logging.error(
            'The secret key file already exists, use the --force flag to overwrite it',
        )
        return 1

    try:
        with open(opt.path, 'w') as fout:
            generated_key = ''.join(
                [
                    random.SystemRandom().choice(
                        string.ascii_letters + string.digits + string.punctuation,
                    )
                    for _ in range(opt.length)
                ],
            )
            fout.write(generated_key)
    except OSError as e:
        logging.error('Unable to create secret key file: %s (%s)', opt.path, e)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
