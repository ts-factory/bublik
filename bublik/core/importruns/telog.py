# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import json
import logging
import os
import subprocess

from django.conf import settings


logger = logging.getLogger('bublik.server')


class ConverterError(Exception):
    '''
    Converter Error exception class that incapsulates internal errors
    '''

    def __init__(self, log_type, log_path='', exception='', action='unable to convert'):
        self.action = action
        self.log_type = log_type
        self.log_path = log_path
        self.exception = exception
        super().__init__(self.exception)

    def __str__(self):
        return f'{self.action} {self.log_type}: {self.log_path} ({self.exception})'


class LogConverter:
    '''
    Abstract log converter that converts path_in into path_out
    by executing an external conversion command.
    '''

    def __init__(self, log_type, path_in=None, path_out=None):
        assert log_type

        self.log_type = log_type
        self.path_in = path_in
        self.path_out = path_out

    def convert_cmd(self):
        raise AssertionError

    def convert(self):
        cmd = self.convert_cmd()
        logger.info('running command: %s', cmd)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
            )
            if proc.wait() != 0:
                output, error = proc.communicate()
                logger.error(
                    f'Failed conversion command: {cmd}\n'
                    f'Output: {output}\n'
                    f'Error: {error}',
                )
                raise ConverterError(
                    self.log_type,
                    self.path_in,
                    Exception(f'Failed conversion command: {cmd}'),
                )

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise ConverterError(self.log_type, self.path_in, e) from ConverterError

        return self


class GZipLog(LogConverter):
    '''
    Class to decompress GZip files.
    '''

    FMT_GZIP_DECOMPRESS = "gzip -d -k '{path_in}' -c > '{path_out}'"

    def __init__(self, path_in=None, path_out=None):
        assert path_in
        assert path_in.endswith('.gz')

        if not path_out:
            path_out = path_in[: -len('.gz')]
        super().__init__(log_type='gz', path_in=path_in, path_out=path_out)

    def convert_cmd(self):
        return GZipLog.FMT_GZIP_DECOMPRESS.format(path_in=self.path_in, path_out=self.path_out)


class BZip2Log(LogConverter):
    '''
    Class to decompress BZip2 files.
    '''

    FMT_BZIP2_DECOMPRESS = "bzip2 -d -k '{path_in}' -c > '{path_out}'"

    def __init__(self, path_in=None, path_out=None):
        assert path_in
        assert path_in.endswith('.bz2')

        if not path_out:
            path_out = path_in[: -len('.bz2')]
        super().__init__(log_type='bz2', path_in=path_in, path_out=path_out)

    def convert_cmd(self):
        return BZip2Log.FMT_BZIP2_DECOMPRESS.format(
            path_in=self.path_in,
            path_out=self.path_out,
        )


class XZLog(LogConverter):
    '''
    Class to decompress XZ files.
    '''

    FMT_XZ_DECOMPRESS = "xz -d -k '{path_in}' -c > '{path_out}'"

    def __init__(self, path_in=None, path_out=None):
        assert path_in
        assert path_in.endswith('.xz')

        if not path_out:
            path_out = path_in[: -len('.xz')]
        super().__init__(log_type='xz', path_in=path_in, path_out=path_out)

    def convert_cmd(self):
        return XZLog.FMT_XZ_DECOMPRESS.format(path_in=self.path_in, path_out=self.path_out)


class XMLLog(LogConverter):
    '''
    This class keeps a temporary file for XML log and provides interfaces
    to convert this log.
    '''

    FMT_XML_PARSER = "{path_xml_parser} '{path_in}' > '{path_out}'"

    def __init__(self, path_in=None, path_out=None):
        assert path_in

        if not path_out:
            path_out = os.path.join(os.path.dirname(path_in), 'log.json')
        super().__init__(log_type='xml', path_in=path_in, path_out=path_out)

    def convert_cmd(self):
        path_xml_parser = os.path.join(settings.BASE_DIR, 'scripts', 'xml_log_parser')

        return XMLLog.FMT_XML_PARSER.format(
            path_xml_parser=path_xml_parser,
            path_in=self.path_in,
            path_out=self.path_out,
        )


class RawLog(LogConverter):
    '''
    This class keeps a temporary file for raw log and provides interfaces
    to convert this log.
    '''

    FMT_RGT_CONV = "te-trc-log --mi-meta '{path_in}' '{path_out}'"

    def __init__(self, path_in=None, path_out=None):
        assert path_in

        if not path_out:
            path_out = os.path.join(os.path.dirname(path_in), 'log.xml')
        super().__init__(log_type='raw_log', path_in=path_in, path_out=path_out)

    def convert_cmd(self):
        return RawLog.FMT_RGT_CONV.format(path_in=self.path_in, path_out=self.path_out)


class RawLogBundle(LogConverter):
    '''
    This class keeps a temporary file for raw log bundle and provides
    interfaces to convert this log.
    '''

    FMT_RGT_LOG_ORIGINAL = (
        "rgt-log-bundle-get-original --bundle='{path_in}' --raw-log='{path_out}'"
    )

    def __init__(self, path_in=None, path_out=None):
        assert path_in

        if not path_out:
            path_out = os.path.join(os.path.dirname(path_in), 'tmp_raw_log')
        super().__init__(log_type='raw_log_bundle', path_in=path_in, path_out=path_out)

    def convert_cmd(self):
        return RawLogBundle.FMT_RGT_LOG_ORIGINAL.format(
            path_in=self.path_in,
            path_out=self.path_out,
        )


class JSONLog:
    '''
    This class keeps a temporary file for JSON log and provides interfaces
    to unpack and load this log.
    '''

    def __init__(self, process_dir=None, json_filename='log.json'):
        self.path_json_log = None
        self.process_dir = process_dir
        self.json_filename = json_filename
        if self.process_dir:
            self.path_json_log = os.path.join(self.process_dir, self.json_filename)

    def load(self, json_filename=None):
        if json_filename:
            self.path_json_log = os.path.join(self.process_dir, self.json_filename)

        with open(self.path_json_log) as json_file:
            return json.load(json_file)

    def convert_from_xz_json_log(self, from_filename):
        XZLog(
            path_in=os.path.join(self.process_dir, from_filename),
            path_out=self.path_json_log,
        ).convert()

        return self.load()

    def convert_from_xz_xml_log(self, from_filename):
        xzlog = XZLog(path_in=os.path.join(self.process_dir, from_filename)).convert()
        XMLLog(path_in=xzlog.path_out, path_out=self.path_json_log).convert()

        return self.load()

    def convert_from_raw_log_bundle(self, from_filename):
        raw_log_bundle = RawLogBundle(
            path_in=os.path.join(self.process_dir, from_filename),
        ).convert()
        raw_log = RawLog(path_in=raw_log_bundle.path_out).convert()
        XMLLog(path_in=raw_log.path_out, path_out=self.path_json_log).convert()

        return self.load()

    def convert_from_bublik_xml(self, from_filename):
        XMLLog(path_in=os.path.join(self.process_dir, from_filename), path_out=self.path_json_log).convert()

        return self.load()

    def convert_from_dir(self, process_dir=None, json_filename=None):
        if json_filename:
            self.json_filename = json_filename
        if process_dir:
            self.process_dir = process_dir
            self.path_json_log = os.path.join(self.process_dir, self.json_filename)

        if os.path.exists(os.path.join(self.process_dir, 'bublik.xml')):
            return self.convert_from_bublik_xml('bublik.xml')
        if os.path.exists(os.path.join(self.process_dir, 'log.json.xz')):
            return self.convert_from_xz_json_log('log.json.xz')
        if os.path.exists(os.path.join(self.process_dir, 'log.xml.xz')):
            return self.convert_from_xz_xml_log('log.xml.xz')
        if os.path.exists(os.path.join(self.process_dir, 'raw_log_bundle.tpxz')):
            return self.convert_from_raw_log_bundle('raw_log_bundle.tpxz')
        return None
