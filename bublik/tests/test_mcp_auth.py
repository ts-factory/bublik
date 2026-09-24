# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
End-to-end tests for MCP authentication, run through the real ASGI app so the
HTTP middleware is exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from unittest import mock

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from django.test.utils import override_settings
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
import httpx
import pytest
from rest_framework import status

from bublik.core.auth import bind_acting_user, current_acting_user
from bublik.core.user_token import UserTokenService
from bublik.data.models import (
    Config,
    ConfigTypes,
    MetaResult,
    MetaTest,
    Project,
    Test,
    TestIterationResult,
    TokenStatus,
    User,
    UserRoles,
    UserToken,
)
from bublik.mcp import tools
from bublik.mcp.auth import build_auth_middleware
from bublik.mcp.server import create_mcp_server


BASE_URL = 'http://mcp.test'
MCP_URL = f'{BASE_URL}/mcp/'

_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}


class McpAuthTestCase(TransactionTestCase):
    """Base class wiring a FastMCP app onto an in-process ASGI client.

    TransactionTestCase: tools query the DB from ``sync_to_async`` worker threads.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )

    def _token_for(self, user, name='mcp'):
        return UserTokenService.issue(user, name).value

    def _app(self):
        mcp = create_mcp_server()
        return mcp.http_app(middleware=build_auth_middleware())

    def _client(self, app, token=None):
        def factory(headers=None, auth=None, follow_redirects=True, timeout=None, **kwargs):
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
                headers=headers,
                auth=auth,
                follow_redirects=follow_redirects,
                timeout=timeout,
            )

        headers = {'Authorization': f'Bearer {token}'} if token else None
        return Client(
            transport=StreamableHttpTransport(
                url=MCP_URL,
                headers=headers,
                httpx_client_factory=factory,
            ),
        )

    async def _session(self, app, token, body):
        """Run ``body(client)`` inside a live MCP session against ``app``."""
        async with app.router.lifespan_context(app), self._client(app, token) as client:
            return await body(client)

    def run_session(self, token, body, app=None):
        return async_to_sync(self._session)(app or self._app(), token, body)

    def raw_post(self, payload, headers):
        app = self._app()

        async def call():
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
            ) as http:
                return await http.post(MCP_URL, json=payload, headers=headers)

        return async_to_sync(call)()


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpAnonymousAccessTest(McpAuthTestCase):
    def test_read_tools_still_work_without_a_token(self):
        async def body(client):
            return await client.call_tool('get_server_version', {})

        result = self.run_session(None, body)
        assert result.data is not None

    def test_the_write_tool_is_listed_but_refuses_without_a_token(self):
        async def body(client):
            listed = await client.list_tools()
            names = [tool.name for tool in listed]
            result = await client.call_tool(
                'edit_test_comment',
                {'test_id': 1, 'project_id': 1, 'comment': 'nope'},
                raise_on_error=False,
            )
            return names, result

        names, result = self.run_session(None, body)
        assert 'edit_test_comment' in names
        assert result.is_error
        assert 'personal access token' in str(result.content)


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpBadCredentialTest(McpAuthTestCase):
    INITIALIZE: ClassVar[dict] = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2025-06-18',
            'capabilities': {},
            'clientInfo': {'name': 'test', 'version': '1'},
        },
    }
    ACCEPT = 'application/json, text/event-stream'

    def _post(self, authorization):
        return self.raw_post(
            self.INITIALIZE,
            {'Authorization': authorization, 'Accept': self.ACCEPT},
        )

    def test_an_unknown_token_is_401_not_silent_anonymous_access(self):
        response = self._post('Bearer bpat_totally-made-up')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'Invalid access token' in response.text

    def test_a_revoked_token_says_so(self):
        raw = self._token_for(self.user)
        token = UserToken.objects.get(user=self.user)
        token.revoke(revoked_by=self.user)
        assert token.status == TokenStatus.REVOKED

        response = self._post(f'Bearer {raw}')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'Access token revoked' in response.text

    def test_a_malformed_header_is_401(self):
        response = self._post('Bearer not-a-bublik-token')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'Malformed Authorization header' in response.text
        assert '\\"Bearer bpat_...\\"' in response.headers['WWW-Authenticate']

    def test_the_value_is_not_echoed_back(self):
        raw = self._token_for(self.user)
        UserToken.objects.get(user=self.user).revoke(revoked_by=self.user)
        response = self._post(f'Bearer {raw}')
        assert raw not in response.text


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpWriteToolTest(McpAuthTestCase):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name='proj')
        self.test = Test.objects.create(name='some-test')

    def _comment(self, token, text='from an agent'):
        async def body(client):
            return await client.call_tool(
                'edit_test_comment',
                {
                    'test_id': self.test.id,
                    'project_id': self.project.id,
                    'comment': text,
                },
                raise_on_error=False,
            )

        return self.run_session(token, body)

    def test_an_admin_token_creates_the_comment(self):
        result = self._comment(self._token_for(self.admin))
        assert not result.is_error, result.content
        assert MetaTest.objects.filter(
            test=self.test,
            project=self.project,
            meta__type='comment',
        ).exists()

    def test_the_token_owners_permissions_are_what_apply(self):
        result = self._comment(self._token_for(self.user))
        assert result.is_error
        assert 'not allowed' in str(result.content)
        assert not MetaTest.objects.filter(test=self.test).exists()

    def test_using_a_token_records_when_it_was_last_used(self):
        raw = self._token_for(self.admin)
        assert UserToken.objects.get(user=self.admin).last_used_at is None
        self._comment(raw)
        assert UserToken.objects.get(user=self.admin).last_used_at is not None


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpResponseCacheTest(McpAuthTestCase):
    """The response cache must never be shared between callers."""

    def _call_get_version(self, app, token, counter):
        async def body(client):
            return await client.call_tool('get_server_version', {})

        with mock.patch.object(
            tools.ServerService,
            'get_version',
            side_effect=lambda: counter.append(1) or {'version': '1.0'},
        ):
            return self.run_session(token, body, app=app)

    def test_a_cached_read_is_not_shared_between_tokens(self):
        app = self._app()
        calls = []

        token_a = self._token_for(self.user, name='a')
        token_b = self._token_for(self.admin, name='b')

        self._call_get_version(app, token_a, calls)
        assert len(calls) == 1, 'the first call should reach the service'

        # Ensures caching is on, so the checks below are not vacuous.
        self._call_get_version(app, token_a, calls)
        assert len(calls) == 1, 'a repeat call by the same token should be cached'

        expected_after_second_token = 2
        self._call_get_version(app, token_b, calls)
        assert len(calls) == expected_after_second_token, (
            "another token must not receive the first user's entry"
        )

        expected_after_anonymous = 3
        self._call_get_version(app, None, calls)
        assert len(calls) == expected_after_anonymous, (
            'an anonymous caller must not receive a token holder entry'
        )

    def test_write_tools_are_excluded_from_the_cache(self):
        assert 'edit_test_comment' in tools.MCP_WRITE_TOOL_NAMES

        project = Project.objects.create(name='proj')
        test = Test.objects.create(name='some-test')
        token = self._token_for(self.admin)
        app = self._app()

        def comment(text):
            async def body(client):
                return await client.call_tool(
                    'edit_test_comment',
                    {'test_id': test.id, 'project_id': project.id, 'comment': text},
                    raise_on_error=False,
                )

            return self.run_session(token, body, app=app)

        assert not comment('first').is_error
        # A cached write would return the first result instead of an error.
        assert comment('first').is_error
        assert comment('second').is_error is False
        expected_comments = 2
        assert MetaTest.objects.filter(test=test).count() == expected_comments

    def test_admin_tools_are_excluded_from_the_cache(self):
        assert 'list_configs' in tools.MCP_ADMIN_TOOL_NAMES

        token = self._token_for(self.admin)
        app = self._app()

        def list_configs():
            async def body(client):
                return await client.call_tool('list_configs', {}, raise_on_error=False)

            return self.run_session(token, body, app=app)

        assert list_configs().data['configs'] == []
        Config.objects.create(
            type=ConfigTypes.SCHEDULE,
            name='nightly',
            project=None,
            is_active=True,
            description='',
            user=self.admin,
            content={},
        )
        # A cached read would still report the empty list.
        assert len(list_configs().data['configs']) == 1


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpCompromisedToolTest(McpAuthTestCase):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name='proj')
        now = datetime.now(tz=timezone.utc)
        self.run = TestIterationResult.objects.create(
            start=now,
            finish=now,
            project=self.project,
        )

    def _call(self, token, name, arguments):
        async def body(client):
            return await client.call_tool(name, arguments, raise_on_error=False)

        # Marking schedules a Celery task; there is no broker in tests.
        with mock.patch('bublik.core.run.compromised.meta_categorization'):
            return self.run_session(token, body)

    def test_anonymous_callers_are_refused(self):
        result = self._call(
            None,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': True, 'comment': 'x'},
        )
        assert result.is_error
        assert 'personal access token' in str(result.content)

    def test_a_plain_user_can_mark_remark_and_clear(self):
        token = self._token_for(self.user)

        marked = self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': True, 'comment': 'broken agent'},
        )
        assert not marked.is_error, marked.content
        assert marked.data['status'] is True
        assert marked.data['comment'] == 'broken agent'
        assert MetaResult.objects.filter(result=self.run, meta__name='compromised').exists()

        # Marking again replaces the mark.
        again = self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': True, 'comment': 'new reason'},
        )
        assert not again.is_error, again.content
        assert again.data['comment'] == 'new reason'
        assert MetaResult.objects.filter(result=self.run, meta__name='compromised').count() == 1

        cleared = self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': False},
        )
        assert not cleared.is_error, cleared.content
        assert cleared.data['status'] is False
        assert not MetaResult.objects.filter(result=self.run, meta__name='compromised').exists()

        assert not self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': False},
        ).is_error

    def test_the_run_comment_is_set_replaced_and_removed(self):
        token = self._token_for(self.user)

        first = self._call(token, 'edit_run_comment', {'run_id': self.run.id, 'comment': 'one'})
        assert not first.is_error, first.content
        assert first.data['comment'] == 'one'

        second = self._call(
            token, 'edit_run_comment', {'run_id': self.run.id, 'comment': 'two'}
        )
        assert second.data['comment'] == 'two'
        assert MetaResult.objects.filter(result=self.run, meta__type='comment').count() == 1

        removed = self._call(token, 'edit_run_comment', {'run_id': self.run.id})
        assert not removed.is_error, removed.content
        assert removed.data['comment'] is None
        assert not MetaResult.objects.filter(result=self.run, meta__type='comment').exists()
        assert not self._call(token, 'edit_run_comment', {'run_id': self.run.id}).is_error

    def test_a_rejected_remark_keeps_the_existing_mark(self):
        token = self._token_for(self.user)
        self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': True, 'comment': 'original'},
        )

        result = self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': True, 'comment': ''},
        )

        assert result.is_error
        assert MetaResult.objects.filter(result=self.run, meta__name='compromised').exists()

    def test_a_comment_is_required(self):
        token = self._token_for(self.user)
        result = self._call(
            token,
            'set_run_compromised',
            {'run_id': self.run.id, 'compromised': True, 'comment': ''},
        )
        assert result.is_error
        assert 'comment is required' in str(result.content)


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpTestCommentToolsTest(McpAuthTestCase):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name='proj')
        self.test = Test.objects.create(name='some-test')
        self.token = self._token_for(self.admin)

    def _call(self, name, **arguments):
        async def body(client):
            return await client.call_tool(
                name,
                {'test_id': self.test.id, 'project_id': self.project.id, **arguments},
                raise_on_error=False,
            )

        return self.run_session(self.token, body)

    def test_list_edit_and_delete(self):
        added = self._call('edit_test_comment', comment='first')
        assert not added.is_error, added.content

        listed = self._call('get_test_comments')
        assert [row['comment'] for row in listed.data] == ['first']
        comment_id = listed.data[0]['comment_id']
        assert comment_id == added.data['comment_id']
        assert comment_id == MetaTest.objects.get(test=self.test).meta_id

        edited = self._call('edit_test_comment', comment_id=comment_id, comment='second')
        assert not edited.is_error, edited.content
        assert edited.data['comment'] == 'second'
        assert edited.data['comment_id'] != comment_id
        assert MetaTest.objects.filter(test=self.test).count() == 1

        deleted = self._call('edit_test_comment', comment_id=edited.data['comment_id'])
        assert not deleted.is_error, deleted.content
        assert deleted.data == {'removed': edited.data['comment_id']}
        assert not MetaTest.objects.filter(test=self.test).exists()

    def test_a_read_after_a_write_is_not_stale(self):
        app = self._app()

        def call(name, **arguments):
            async def body(client):
                return await client.call_tool(
                    name,
                    {'test_id': self.test.id, 'project_id': self.project.id, **arguments},
                    raise_on_error=False,
                )

            return self.run_session(self.token, body, app=app)

        assert call('get_test_comments').data == []
        call('edit_test_comment', comment='fresh')
        assert [row['comment'] for row in call('get_test_comments').data] == ['fresh']

    def test_an_unknown_comment_is_reported(self):
        result = self._call('edit_test_comment', comment_id=999999)
        assert result.is_error
        assert 'not found' in str(result.content)

    def test_nothing_to_do_is_reported(self):
        result = self._call('edit_test_comment')
        assert result.is_error
        assert 'comment text' in str(result.content)
        assert not MetaTest.objects.filter(test=self.test).exists()


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpConfigToolsTest(McpAuthTestCase):
    def _call(self, token, name, arguments=None):
        async def body(client):
            return await client.call_tool(name, arguments or {}, raise_on_error=False)

        return self.run_session(token, body)

    def test_a_plain_user_is_refused_even_for_reads(self):
        token = self._token_for(self.user)
        result = self._call(token, 'list_configs')
        assert result.is_error
        assert 'administrators' in str(result.content)

    def test_admin_tools_are_listed_for_everyone(self):
        async def body(client):
            return [tool.name for tool in await client.list_tools()]

        names = self.run_session(None, body)
        for name in tools.MCP_ADMIN_TOOL_NAMES:
            assert name in names

    def test_an_admin_can_create_edit_and_delete(self):
        token = self._token_for(self.admin)

        listed = self._call(token, 'list_configs')
        assert listed.data['configs'] == []
        assert any(
            entry['type'] == ConfigTypes.SCHEDULE
            for entry in listed.data['available_types_names']
        )

        created = self._call(
            token,
            'edit_config',
            {
                'config_type': ConfigTypes.SCHEDULE,
                'name': 'nightly',
                'content': {'note': 'v1'},
                'description': 'first',
            },
        )
        assert not created.is_error, created.content
        assert created.data['created_new_version'] is True
        assert created.data['is_active'] is True
        assert Config.objects.get(pk=created.data['id']).user == self.admin

        edited = self._call(
            token,
            'edit_config',
            {'config_id': created.data['id'], 'content': {'note': 'v2'}},
        )
        assert not edited.is_error, edited.content
        assert edited.data['created_new_version'] is True
        assert edited.data['version'] == 1
        assert edited.data['is_active'] is True

        fetched = self._call(token, 'get_config', {'config_id': edited.data['id']})
        assert fetched.data['content'] == {'note': 'v2'}
        expected_versions = 2
        assert len(fetched.data['versions']) == expected_versions

        reactivated = self._call(
            token,
            'edit_config',
            {'config_id': created.data['id'], 'is_active': True},
        )
        assert not reactivated.is_error, reactivated.content
        assert reactivated.data['created_new_version'] is False
        assert reactivated.data['is_active'] is True

        deleted = self._call(token, 'delete_config', {'config_id': edited.data['id']})
        assert not deleted.is_error, deleted.content
        assert Config.objects.count() == 1

    def test_creating_without_the_required_fields_is_reported(self):
        token = self._token_for(self.admin)
        result = self._call(token, 'edit_config', {'name': 'nightly'})
        assert result.is_error
        assert 'config_type' in str(result.content)
        assert 'content' in str(result.content)
        assert Config.objects.count() == 0

    def test_invalid_content_is_rejected_with_the_schema_error(self):
        token = self._token_for(self.admin)
        result = self._call(
            token,
            'edit_config',
            {
                'config_type': ConfigTypes.GLOBAL,
                'name': 'per_conf',
                'content': {'NOT_A_KEY': 1},
            },
        )
        assert result.is_error
        assert 'Invalid format' in str(result.content)

    def test_the_schema_is_served(self):
        token = self._token_for(self.admin)
        result = self._call(
            token,
            'get_config_schema',
            {'config_type': ConfigTypes.GLOBAL, 'config_name': 'per_conf'},
        )
        assert not result.is_error, result.content
        assert 'NOT_PERMISSION_REQUIRED_ACTIONS' in result.data['properties']


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpProjectToolsTest(McpAuthTestCase):
    def _call(self, token, name, arguments):
        async def body(client):
            return await client.call_tool(name, arguments, raise_on_error=False)

        return self.run_session(token, body)

    def test_a_plain_user_is_refused(self):
        token = self._token_for(self.user)
        result = self._call(token, 'edit_project', {'name': 'proj'})
        assert result.is_error
        assert 'administrators' in str(result.content)
        assert not Project.objects.exists()

    def test_an_admin_can_create_rename_and_delete(self):
        token = self._token_for(self.admin)

        created = self._call(token, 'edit_project', {'name': 'proj'})
        assert not created.is_error, created.content
        assert created.data['name'] == 'proj'
        project_id = created.data['id']

        duplicate = self._call(token, 'edit_project', {'name': 'proj'})
        assert duplicate.is_error

        renamed = self._call(
            token, 'edit_project', {'name': 'renamed', 'project_id': project_id}
        )
        assert not renamed.is_error, renamed.content
        assert renamed.data == {'id': project_id, 'name': 'renamed'}
        assert Project.objects.get(pk=project_id).name == 'renamed'

        deleted = self._call(token, 'delete_project', {'project_id': project_id})
        assert not deleted.is_error, deleted.content
        assert not Project.objects.filter(pk=project_id).exists()

    def test_a_project_with_runs_cannot_be_deleted(self):
        token = self._token_for(self.admin)
        project = Project.objects.create(name='proj')
        now = datetime.now(tz=timezone.utc)
        TestIterationResult.objects.create(start=now, finish=now, project=project)

        result = self._call(token, 'delete_project', {'project_id': project.id})
        assert result.is_error
        assert 'runs linked' in str(result.content)
        assert Project.objects.filter(pk=project.id).exists()


class McpToolRegistryTest(TransactionTestCase):
    def test_reads_of_changeable_data_are_uncached_reads(self):
        read = {tool.__name__ for tool in tools.MCP_TOOLS}
        assert set(tools.MCP_UNCACHED_TOOL_NAMES) <= read

    def test_the_three_registries_are_disjoint(self):
        read = {tool.__name__ for tool in tools.MCP_TOOLS}
        write = {tool.__name__ for tool in tools.MCP_WRITE_TOOLS}
        admin = {tool.__name__ for tool in tools.MCP_ADMIN_TOOLS}
        assert not read & write
        assert not read & admin
        assert not write & admin


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpActingUserTest(TransactionTestCase):
    """The write tools accept an in-process caller bound by the chat agent."""

    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )
        self.project = Project.objects.create(name='proj')
        self.test = Test.objects.create(name='some-test')

    def _comment(self, text):
        return async_to_sync(tools.edit_test_comment)(
            test_id=self.test.id,
            project_id=self.project.id,
            comment=text,
        )

    def test_a_bound_admin_can_write(self):
        with bind_acting_user(self.admin.id):
            result = self._comment('bound')

        assert result['comment'] == 'bound'
        assert MetaTest.objects.filter(test=self.test).count() == 1

    def test_a_bound_plain_user_is_refused_by_the_project_rule(self):
        with bind_acting_user(self.user.id), pytest.raises(ToolError, match='not allowed'):
            self._comment('nope')

        assert not MetaTest.objects.filter(test=self.test).exists()

    def test_nobody_bound_is_refused(self):
        with pytest.raises(ToolError, match='personal access token'):
            self._comment('nope')

    def test_a_deactivated_bound_user_is_refused(self):
        self.admin.is_active = False
        self.admin.save(update_fields=['is_active'])

        with bind_acting_user(self.admin.id), pytest.raises(ToolError, match='access token'):
            self._comment('nope')

    def test_the_binding_does_not_leak_past_the_block(self):
        with bind_acting_user(self.admin.id):
            assert current_acting_user() == self.admin
        assert current_acting_user() is None
