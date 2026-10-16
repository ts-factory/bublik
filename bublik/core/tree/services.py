# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from bublik.core.cache import RunCache
from bublik.core.result.services import ResultService
from bublik.core.tree.dto import TreeDTO, TreeNodeDTO
from bublik.core.tree.representation import path_to_node, tree_representation


class TreeService:
    @staticmethod
    def get_tree(run_id: int) -> TreeDTO:
        """
        Get tree representation of a run.

        Args:
            run_id: The ID of the test run

        Returns:
            DTO with nodes keyed by ID and an optional main package ID

        Raises:
            NotFoundError: if run not found
        """
        result = ResultService.get_result(run_id)

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

        return TreeDTO(
            tree={
                node_id: TreeNodeDTO(
                    id=node['id'],
                    start=node['start'],
                    name=node['name'],
                    entity=node['entity'],
                    has_error=node['has_error'],
                    skipped=node['skipped'],
                    children=node.get('children'),
                )
                for node_id, node in tree_dict.items()
            },
            main_package=main_package,
        )

    @staticmethod
    def get_tree_path(result_id: int) -> list[int]:
        """
        Get path from root to a specific node.

        Args:
            result_id: The ID of the test result

        Returns:
            List of node IDs from root to result

        Raises:
            NotFoundError: if result not found
        """
        result = ResultService.get_result(result_id)
        return path_to_node(result)
