# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import defaultdict
from datetime import datetime
import logging
import os

from subprocess import PIPE, STDOUT, Popen

from bublik.core.run.external_links import get_trc_brief
from bublik.core.run.filter_expression import TestRunMeta
from bublik.core.run.stats import get_run_stats_detailed, get_test_runs
from bublik.data.models import MetaResult, TestIteration


logger = logging.getLogger('bublik.server')


def packages_path_to_str(packages, package_strip):
    res = []
    for path in packages:
        line = '/'.join(path)
        if package_strip:
            line = line.replace(package_strip, '')
        res.append(line)
    return res


def groups_to_str(tag_groups):
    return ', '.join(str(group) for group in tag_groups)


class TestingCoverageRow:
    def __init__(self, tags=None, tag_groups=None):
        super().__init__()

        if tag_groups is None:
            tag_groups = []
        if tags is None:
            tags = []
        self.tags = tags
        self.tag_groups = tag_groups
        self.packages = defaultdict(int)
        self.links = []

    def __eq__(self, other):
        return (
            self.tags.__dict__ == other.tags.__dict__
            and self.tag_groups.__dict__ == other.tag_groups.__dict__
        )

    def __lt__(self, other):
        if len(self.tags) > 0 and len(other.tags) == 0:
            return True
        if self.tags == other.tags:
            return str(self.tag_groups) < str(other.tag_groups)
        return self.tags < other.tags

    def __repr__(self):
        return f'TestingCoverageRow(tags={self.tags}, packages={self.packages})'


class TestingCoverage:
    '''
    The object contains data of testing coverage structured in dependence on
    specified tags which are considered to be significant. It can be used to
    generate pdf or html reports.
    '''

    def __init__(
        self,
        tags=None,
        tag_groups=None,
        package_strip=None,
        package_ignore=None,
        debug=False,
    ):
        super().__init__()

        if tag_groups is None:
            tag_groups = {}
        if tags is None:
            tags = {}
        self.tags = tags
        self.tag_groups = tag_groups
        self.package_strip = package_strip
        self.package_ignore = package_ignore
        self.debug = debug

        self.rows = []
        self.path_report_main = 'doc/report/report.tex'
        self.path_report_content = 'report_content.tex'

    def __del__(self):
        return

    def __repr__(self):
        return f'TestingCoverage(tags={self.tags}, rows={self.rows})'

    def get_row(self, key_tags, key_tag_groups):
        for row in self.rows:
            if row.tags == key_tags and row.tag_groups == key_tag_groups:
                return row

        return None

    def add(self, run_tags, packages, link=None):
        key_tags = []
        key_tag_groups = []

        def ingroup(tag):
            for group in self.tag_groups:
                if tag in group.tags:
                    if group not in key_tag_groups:
                        key_tag_groups.append(group)
                    return True
            return None

        for tag in run_tags:
            if not ingroup(tag) and tag in self.tags:
                key_tags.append(tag)

        row = self.get_row(key_tags, key_tag_groups)
        if not row:
            row = TestingCoverageRow(key_tags, key_tag_groups)
            self.rows.append(row)

        if link:
            row.links.append(link)

        for package in packages_path_to_str(packages, self.package_strip):
            row.packages[package] += 1

    def get(self, start_date, finish_date, min_ok, run_ignore_label):
        logger.info('Retrieve test runs data')

        self.start_date = start_date
        self.finish_date = finish_date
        runs = get_test_runs(
            start_date=start_date,
            finish_date=finish_date,
            exclude_label=run_ignore_label,
        )

        tags = MetaResult.objects.filter(meta__type='tag').prefetch_related('meta')

        for run in runs:
            logger.info('Test run: %d' % (run.id))

            results = get_run_stats_detailed(run.id)

            run_packages = []
            for package in results['children']:
                if package['stats']['passed'] + package['stats']['failed'] >= min_ok:
                    run_packages.append(package['path'])

            run_tags = []
            for tag in tags.filter(result=run):
                run_tags.append(TestRunMeta(tag.meta.name, tag.meta.value))

            if self.debug:
                link_to_trc_brief = get_trc_brief(run)
                self.add(sorted(run_tags), run_packages, link_to_trc_brief)
            else:
                self.add(sorted(run_tags), run_packages)

            # ~ REMOVE
            # ~ break

    def generate_pdf(self, table_max_lines, first_column_width):
        def put_table_head(repf, columns_c, table_head):
            repf.write('\\rowcolors{1}{}{tablerowcolor}\n')
            repf.write('\\resizebox{\\columnwidth}{!}{%\n')
            repf.write('\\begin{tabular}{p{%dcm}|%s}\n' % (first_column_width, columns_c))
            repf.write(f'    {table_head}\\\\\n')
            repf.write('    \\midrule\n')

        def put_table_tail(repf):
            repf.write('    \\bottomrule\n')
            repf.write('\\end{tabular}\n')
            repf.write('} \\clearpage\n')

        def generate_tables(packages, package_stats):
            with open(self.path_report_content, 'w') as repf:

                repf.write(
                    f'\\lfoot{{Report for the period from {datetime.date(self.start_date)} to '
                    f'{datetime.date(self.finish_date)}}}\n\n',
                )

                debug_buf = ''
                columns_c = ''
                table_head = 'Tags combinations'

                if self.debug:
                    columns_c += 'c|'
                    table_head += '&Id'

                for package in packages:
                    columns_c = columns_c + 'c'
                    table_head = table_head + '& \\rot{' + package.replace('_', '\\_') + '} \n'

                for i, row in enumerate(sorted(self.rows)):
                    if i % table_max_lines == 0:
                        if i != 0:
                            put_table_tail(repf)
                        put_table_head(repf, columns_c, table_head)

                    row_str = ', '.join(str(tag) for tag in row.tags)
                    tag_groups_str = groups_to_str(row.tag_groups)
                    row_str = f'{row_str}, {tag_groups_str}' if row_str else tag_groups_str
                    row_str = row_str.replace('_', '\\_')

                    if self.debug:
                        debug_buf += '\\colorbox{green}{%d|%s:}\n\n' % (i, row_str)
                        for link in row.links:
                            debug_buf += '\\href{{{}}}{{{}}}\n\n'.format(
                                link,
                                link.replace('_', '\\_'),
                            )
                        row_str += '&' + str(i)
                        debug_buf += '\n\n'

                    for package in packages:
                        row_str += '&'
                        if package in row.packages:
                            row_str = row_str + str(row.packages[package])
                        else:
                            row_str = row_str + '-'

                    repf.write(f'    {row_str} \\\\\n')

                if self.debug:
                    row_str = '    \\midrule\n'
                    row_str = '\\textbf{Total}&'
                    for package in packages:
                        row_str += '&' + str(package_stats[package])
                    repf.write(f'    {row_str} \\\\\n')

                put_table_tail(repf)

                if self.debug:
                    repf.write(debug_buf)
                    repf.write(' \\clearpage\n')

        logger.info('Generate PDF report')

        packages = TestIteration.get_packages(self.package_strip, self.package_ignore)

        logger.debug(packages)
        package_stats = defaultdict(int)
        for row in self.rows:
            for package in row.packages:
                package_stats[package] += row.packages[package]

        # Ignore a package without results if it is parent of other packages.
        for i, package in enumerate(packages):
            if not package_stats[package] or package_stats[package] == 0:
                for p in packages:
                    if p.startswith(packages[i] + '/'):
                        packages.remove(package)

        generate_tables(packages, package_stats)

        p = Popen(
            ['latex', '-halt-on-error', '-output-format=pdf', self.path_report_main],
            stdout=PIPE,
            stderr=STDOUT,
        )
        stdout = p.communicate()
        if p.returncode != 0:
            logger.info(stdout)

        logger.info(
            'Report has been generated to the file: %s'
            % (os.path.basename(self.path_report_main).replace('.tex', '.pdf')),
        )
