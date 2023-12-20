# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import copy
import os

from django.db.models import Q
import pyeda.inter
import pyparsing as pp


class TestRunMeta:
    def __init__(self, name, value=None, relation=None):
        super().__init__()

        self.alias = ''

        self.name = str(name) if name else None

        if value:
            self.value = str(value)
            self.relation = '='
        else:
            self.value = None

        self.relation = str(relation) if relation else None

    def __str__(self):
        if self.value:
            return f'{self.name}{self.relation}{self.value}'
        return self.name

    def __repr__(self):
        return str(self)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __lt__(self, other):
        if self.name == other.name:
            return self.value < other.value
        return self.name < other.name

    def __hash__(self):
        return hash(str(self))

    def filter_q(self, negation=False, expr_type=None):
        if expr_type is None:
            expr_type = 'tag'

        def filter_value(val, op, negation):
            op_correspondings = {
                '=': {
                    'negation': '!=',
                    'meta_query': lambda val: Q(meta_results__meta__value=val),
                    'test_arg_query': lambda val: Q(test_arguments__value=val),
                },
                '!=': {
                    'negation': '=',
                    'meta_query': lambda val: ~Q(meta_results__meta__value=val),
                    'test_arg_query': lambda val: ~Q(test_arguments__value=val),
                },
                '<': {
                    'negation': '>=',
                    'meta_query': lambda val: Q(meta_results__meta__value__lt=val),
                    'test_arg_query': lambda val: Q(test_arguments__value__lt=val),
                },
                '<=': {
                    'negation': '>',
                    'meta_query': lambda val: Q(meta_results__meta__value__lte=val),
                    'test_arg_query': lambda val: Q(test_arguments__value__lte=val),
                },
                '>': {
                    'negation': '<=',
                    'meta_query': lambda val: Q(meta_results__meta__value__gt=val),
                    'test_arg_query': lambda val: Q(test_arguments__value__gt=val),
                },
                '>=': {
                    'negation': '<',
                    'meta_query': lambda val: Q(meta_results__meta__value__gte=val),
                    'test_arg_query': lambda val: Q(test_arguments__value__gte=val),
                },
            }

            if negation:
                op = op_correspondings.get(op, {}).get('negation', op)

            if op not in op_correspondings:
                msg = f'Unknown relation value: \'{op}\'. Expected: = / != / < / <= / > / >='
                raise ValueError(msg)

            if expr_type == 'test_argument':
                return op_correspondings[op]['test_arg_query'](val)
            return op_correspondings[op]['meta_query'](val)

        # Create a query corresponding to the expression type and TestRunMeta object
        if expr_type == 'test_argument':
            query = Q(test_arguments__name=self.name)
            query &= filter_value(self.value, self.relation, negation)
        elif expr_type == 'verdict' and self.value == 'None':
            # If the verdict expression is None, return objects without verdicts
            query = ~Q(meta_results__meta__type=expr_type)
        else:
            query = Q(meta_results__meta__type=expr_type)
            if self.name:
                query &= Q(
                    meta_results__meta__name=self.name,
                )
            if self.value:
                query &= filter_value(self.value, self.relation, negation)
            elif negation:
                query = ~Q(query)

        return query


class TestRunMetasGroup:
    def __init__(self, metas=None):
        super().__init__()

        self.metas = []
        # Aliases based dictionary
        self.metasd = {}

        if metas:
            for meta in metas:
                self.metas_append(meta)

    def __str__(self):
        def insubset(metastr, subsets):
            for subset in subsets:
                for mstr in subset:
                    if metastr[:5] in mstr:
                        subset.append(metastr)
                        return True
            return False

        subsets = []
        for meta in self.metas:
            metastr = str(meta)
            if not insubset(metastr, subsets):
                subsets.append([metastr])

        res = []
        for subset in subsets:
            line = os.path.commonprefix(subset)
            length = len(line)
            if len(subset) != 1:
                line += '\\{'
                line += ','.join(meta[length:] for meta in subset)
                line += '\\}'
            res.append(line)

        line = ', '.join(group for group in res)

        if len(subsets) > 1:
            line = '\\{' + line + '\\}'
        return line

    def metas_append(self, meta):
        self.metas.append(meta)
        if meta.alias:
            self.metasd[meta.alias] = meta

    def get_meta_by_alias(self, alias):
        meta = self.metasd.get(alias)
        if not meta:
            msg = f'Failed to get meta by alias \'{alias}\''
            raise ValueError(msg)
        return meta

    def expr_str_to_dnf(self, expr_str, expr_type):
        def create_meta(s, loc, toks):
            if isinstance(toks[0], str):
                if expr_type == 'verdict':
                    meta = TestRunMeta(None, toks[0], '=')
                else:
                    meta = TestRunMeta(toks[0])
            else:
                meta = TestRunMeta(toks[0][0], toks[0][2], toks[0][1])

            for smeta in self.metas:
                if str(smeta) == str(meta):
                    return smeta.alias

            meta.alias = 'talias' + str(len(self.metas))
            self.metas_append(meta)
            return meta.alias

        if not expr_str:
            return None

        if expr_type == 'verdict':
            no_verdict = pp.Word('None')
            verdict = pp.Regex(r'[^"]*')
            verdict_string = pp.Suppress('"') + verdict + pp.Suppress('"')
            condition = no_verdict | verdict_string
        else:
            operator = pp.Regex('>=|<=|!=|>|<|=').setName('operator')
            operator_eq_ne = pp.Regex('=|!=').setName('operator')

            identifier_rev = pp.Combine(pp.Word(pp.alphanums.upper()) + pp.Literal('_REV'))
            identifier_branch = pp.Combine(
                pp.Word(pp.alphanums.upper()) + pp.Literal('_BRANCH'),
            )

            string = pp.Word(pp.alphanums + '._-/%+:')
            string_with_sign = pp.Combine(pp.Literal('!') + string)
            number = pp.Regex(r'[+-]?\d+(:?\.\d*)?(:?[eE][+-]?\d+)?')
            revision = pp.Combine(pp.Word(pp.hexnums) + pp.Optional(pp.Literal('+')))

            # NB! The order makes sense: specific groups must go first
            condition = (
                pp.Group(identifier_rev + operator_eq_ne + revision)
                | pp.Group(identifier_branch + operator_eq_ne + string)
                | pp.Group(string + operator_eq_ne + string)
                | pp.Group(string + operator_eq_ne + string_with_sign)
                | pp.Group(string + operator + number)
                | string
            )

        condition.setParseAction(create_meta)

        prec = pp.operatorPrecedence(
            condition,
            [
                ('!', 1, pp.opAssoc.RIGHT),
                ('&', 2, pp.opAssoc.LEFT),
                ('|', 2, pp.opAssoc.LEFT),
            ],
        )

        try:
            res = prec.parseString(expr_str, parseAll=True)
        except pp.ParseException as pe:
            if expr_type == 'verdict':
                expected = 'None | "Verdict"'
            elif expr_type == 'test_argument':
                expected = 'argument1 != 5 & argument2 >= 10'
            else:
                expected = 'meta_name1 & meta_name2=32'
            msg = (
                f'Faied to parse expression string \'{expr_str}\'. '
                f'Expected example: {expected}',
            )
            raise pp.ParseException(msg) from pe

        line_esc = ''

        def parse_item(item):
            line_local = ''
            if isinstance(item, pp.ParseResults):
                line_local += '('
                for val in item:
                    line_local += parse_item(val)
                line_local += ')'
            else:
                if item == '!':
                    item = '~'
                line_local += item
            return line_local

        line_esc = parse_item(res)
        return pyeda.inter.expr(line_esc).to_dnf()

    def apply_filters(self, qs, expr_dnf, expr_type):
        def filter_meta(qs, var):
            meta = None
            if isinstance(var, pyeda.boolalg.expr.Variable):
                meta = self.get_meta_by_alias(str(var))
                qs = qs.filter(meta.filter_q(expr_type=expr_type))
            elif isinstance(var, pyeda.boolalg.expr.Complement):
                meta = self.get_meta_by_alias(str(var)[1::])
                qs = qs.filter(meta.filter_q(negation=True, expr_type=expr_type))
            elif isinstance(var, pyeda.boolalg.expr.AndOp):
                for item in var.xs:
                    qs = filter_meta(qs, item)
            else:
                msg = f'Unknown variable type {type(var)}: \'{var!s}\''
                raise TypeError(msg)

            return qs

        base_qs = qs
        qs = None

        if isinstance(expr_dnf, pyeda.boolalg.expr.OrOp):
            for item in expr_dnf.xs:
                qs2 = copy.deepcopy(base_qs)
                qs2 = filter_meta(qs2, item)
                qs = qs.union(qs2) if qs is not None else qs2
        else:
            qs = filter_meta(base_qs, expr_dnf)

        return qs


def filter_by_expression(filtered_qs, expr_str, expr_type=None):
    '''
    Filter passed QuerySet objects according to the passed expressions of the specified type.
    '''

    if expr_type is None:
        expr_type = 'tag'
    mg = TestRunMetasGroup()
    expr_dnf = mg.expr_str_to_dnf(expr_str, expr_type)
    return mg.apply_filters(filtered_qs, expr_dnf, expr_type)
