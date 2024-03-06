# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import defaultdict, deque

from django.db.models import Exists, F, OuterRef
from treelib import Tree as TreeLib

from bublik.core.queries import get_or_none
from bublik.data.models import Meta, MetaResult, ResultType, TestIterationResult


class Tree(TreeLib):
    '''The class for tree representation in different formats.'''

    def to_linear_dict(self, with_data=False):
        '''
        Exports the tree in the linear format representing node children
        as an adjacency list.
        '''
        adjacency_list = defaultdict(dict)
        if self.nodes:
            for n in self.expand_tree(mode=self.WIDTH):
                nid = self[n].identifier
                adjacency_list[nid] = defaultdict(list)
                if with_data:
                    adjacency_list[nid].update(self[n].data)
                for c in self.children(nid):
                    cid = c.identifier
                    adjacency_list[nid]['children'].append(cid)
        return adjacency_list


def path_to_node(result):
    path = deque()
    if result.main_package:
        i = result.parent_package
        if i:
            path.append(i.id)
        while i and i.parent_package:
            parent_id = i.parent_package.id
            path.appendleft(parent_id)
            i = get_or_none(TestIterationResult.objects, id=parent_id)

    # Add the node result to the end of its path
    path.append(result.id)

    return list(path)


def tree_representation(result):
    skipped_meta_ids = list(
        Meta.objects.filter(value='SKIPPED').values_list('id', flat=True),
    )

    result_nodes = (
        TestIterationResult.objects.filter(test_run=result.root)
        .select_related('iteration__test')
        .annotate(
            name=F('iteration__test__name'),
            entity=F('iteration__test__result_type'),
            parent_id=F('parent_package__id'),
            has_error=Exists(
                MetaResult.objects.filter(result__id=OuterRef('id'), meta__type='err'),
            ),
            skipped=Exists(
                MetaResult.objects.filter(result__id=OuterRef('id'), meta__in=skipped_meta_ids),
            ),
        )
        .values('id', 'start', 'name', 'entity', 'parent_id', 'has_error', 'skipped')
        .order_by('id')
    )

    tree = Tree()
    for node in result_nodes:
        node.update({'entity': ResultType.inv(node['entity'])})
        tree.create_node(identifier=node['id'], parent=node.pop('parent_id'), data=node)

    return tree
