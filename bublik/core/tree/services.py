# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.core.exceptions import ValidationError

from bublik.core.cache import RunCache
from bublik.core.tree.representation import path_to_node, tree_representation
from bublik.data import models


class TreeService:
    '''
    Service for tree-related operations (shared between REST API and MCP).
    '''

    @staticmethod
    def get_result(result_id: int) -> models.TestIterationResult:
        '''
        Get a result by ID.

        Args:
            result_id: The ID of the test result

        Returns:
            TestIterationResult instance

        Raises:
            ValidationError: if result not found
        '''
        try:
            return models.TestIterationResult.objects.get(id=result_id)
        except models.TestIterationResult.DoesNotExist as e:
            msg = f'Result {result_id} not found'
            raise ValidationError(msg) from e

    @staticmethod
    def get_tree(run_id: int) -> dict:
        '''
        Get tree representation of a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with 'tree' (linear dict) and 'main_package' (int or None)

        Raises:
            ValidationError: if run not found
        '''
        result = TreeService.get_result(run_id)

        # Try to get cached tree
        cache = RunCache.by_obj(result, 'tree')
        tree = cache.data
        if not tree:
            tree = tree_representation(result)
            cache.data = tree

        main_package = None
        if result.main_package:
            main_package = result.main_package.id

        # Handle subtree if result is not root
        if result.root is not result:
            tree = tree.subtree(result.id)
            main_package = result.id

        # Convert to linear dict format
        tree_dict = tree.to_linear_dict(with_data=True)

        return {
            'tree': tree_dict,
            'main_package': main_package,
        }

    @staticmethod
    def get_tree_path(result_id: int) -> list[int]:
        '''
        Get path from root to a specific node.

        Args:
            result_id: The ID of the test result

        Returns:
            List of node IDs from root to result

        Raises:
            ValidationError: if result not found
        '''
        result = TreeService.get_result(result_id)
        return path_to_node(result)
