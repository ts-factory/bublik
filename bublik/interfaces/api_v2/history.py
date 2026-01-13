# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import chain

from django.core.cache import cache
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.history.v2.utils import generate_hashkey


__all__ = [
    'HistoryViewSet',
]


class HistoryViewSet(ListModelMixin, GenericViewSet):
    def _extract_query_params(self, request):
        return {
            'test_name': request.query_params.get('test_name'),
            'project_id': request.query_params.get('project'),
            'from_date': request.query_params.get('from_date', ''),
            'to_date': request.query_params.get('to_date', ''),
            'run_ids': request.query_params.get('run_ids', ''),
            'branches': request.query_params.get('branches', ''),
            'revisions': request.query_params.get('revisions', ''),
            'labels': request.query_params.get('labels', ''),
            'tags': request.query_params.get('tags', ''),
            'branch_expr': request.query_params.get('branch_expr', ''),
            'rev_expr': request.query_params.get('rev_expr', ''),
            'label_expr': request.query_params.get('label_expr', ''),
            'tag_expr': request.query_params.get('tag_expr', ''),
            'run_properties': request.query_params.get('run_properties', ''),
            'iteration_hash': request.query_params.get('hash', ''),
            'test_args': request.query_params.get('test_args', []),
            'test_arg_expr': request.query_params.get('test_arg_expr', ''),
            'result_statuses': request.query_params.get('result_statuses', ''),
            'verdict': request.query_params.get('verdict', ''),
            'verdict_lookup': request.query_params.get('verdict_lookup', ''),
            'verdict_expr': request.query_params.get('verdict_expr', ''),
            'result_types': request.query_params.get('result_types', ''),
        }

        data = {}
        # Apply pagination
        if grouped:
            test_results_by_iteration = group_results_by_iteration(test_results)
            lists_test_results = []
            for _iteration_hash, iteration_results in test_results_by_iteration:
                lists_test_results.append(list(iteration_results))

            test_results_by_iteration = self.paginate_queryset(lists_test_results)
            test_results = chain(*test_results_by_iteration)
            data['test_results'] = test_results_by_iteration
        else:
            test_results = self.paginate_queryset(test_results)
            data['test_results'] = test_results

        # Update results, iterations and runs IDs after pagination
        for result in test_results:
            self.runs_ids.add(result['run_id'])
            self.iterations_ids.add(result['iteration_id'])
            self.results_ids.add(result['id'])

        # Prepare results data by entities they belong
        important_tags, relevant_tags = get_tags_by_runs(self.runs_ids)
        data.update(
            {
                'results': get_results(self.results_ids),
                'verdicts': get_verdicts(self.results_ids),
                'parameters_by_iterations': get_parameters_by_iterations(self.iterations_ids),
                'metadata_by_runs': get_metadata_by_runs(self.runs_ids),
                'important_tags': important_tags,
                'relevant_tags': relevant_tags,
            },
        )

        return data, counts

    def prepare_response(self, response_list, counts, add_context=None):
        '''This is a hand-help method to collect all data for returning in Response object.'''

        if add_context is None:
            add_context = {}
        response = {
            'from_date': self.from_date.isoformat(),
            'to_date': self.to_date.isoformat(),
            'counts': counts,
            'pagination': self.paginator.get_pagination(),
            'results': response_list,
            'results_ids': self.results_ids,
        }

        if add_context:
            response.update(add_context)

        return response

    def list(self, request, pk=None):
        # Try to use response_list from the cache based on the request
        hashkey = generate_hashkey(request)
        response_data = cache.get(hashkey)
        if response_data is not None:
            return Response(response_data)

        # Filter results
        test_results = self.get_queryset()

        # Prepare results data available as instance fields
        data, counts = self.prepare_results_data(test_results)

        # Aggregate test results for response
        response_list = prepare_list_results(
            data['test_results'],
            data['important_tags'],
            data['relevant_tags'],
            data['metadata_by_runs'],
            data['parameters_by_iterations'],
            data['results'],
            data['verdicts'],
        )

        # Prepare response data
        response_data = self.prepare_response(response_list, counts)

        # Save response_list to cache with based on the request key
        cache.set(hashkey, response_data)

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def grouped(self, request, pk=None):
        # Try to use response_list from the cache based on the request
        hashkey = generate_hashkey(request)
        response_data = cache.get(hashkey)
        if response_data is not None:
            return Response(response_data)

        # Get filtered results
        test_results = self.get_queryset()

        # Prepare results data available as instance fields
        data, counts = self.prepare_results_data(test_results, grouped=True)

        # Aggregate test results for response
        response_list = group_results(
            data['test_results'],
            data['important_tags'],
            data['relevant_tags'],
            data['parameters_by_iterations'],
            data['results'],
            data['verdicts'],
        )

        # Prepare response data
        response_data = self.prepare_response(response_list, counts)

        # Save response_list to cache with based on the request key
        cache.set(hashkey, response_data)

        return Response(response_data)
