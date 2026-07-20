# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

import json
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings
from jsonschema import Draft7Validator

from bublik.ai.agent import build_agent
from bublik.ai.config import (
    ModelRequestError,
    config_fingerprint,
    effective_ai_config,
    parse_ai_config,
    public_models,
    resolve_api_key,
    resolve_model_request,
    resolve_secret_reference,
)
from bublik.ai.discovery import (
    _DISCOVERABLE_TYPES,
    enrich_model,
    populate_models,
)
from bublik.ai.mcp import build_mcp_toolsets, resolve_mcp_headers
from bublik.ai.types import AiConfig, McpServer, ModelEntry, Provider
import bublik.data


def _config(**model_extra):
    return {
        'providers': [
            {
                'id': 'proxy',
                'type': 'openai',
                'api_url': 'http://localhost:9/v1',
                'api_key': '${settings:AI_TEST_API_KEY}',
                'models': [{'id': 'test-model', **model_extra}],
            },
        ],
        'default_model': {'provider': 'proxy', 'model': 'test-model'},
    }


def _provider(**extra):
    return Provider.model_validate({'id': 'proxy', 'type': 'openai', **extra})


class ResolveApiKeyTest(SimpleTestCase):
    @override_settings(AI_TEST_API_KEY='sekret')
    def test_resolves_explicit_settings_source(self):
        self.assertEqual(
            resolve_api_key(_provider(api_key='${settings:AI_TEST_API_KEY}')),
            'sekret',
        )

    @mock.patch.dict('os.environ', {'AI_TEST_API_KEY': 'env-secret'})
    @override_settings(AI_TEST_API_KEY='settings-secret')
    def test_resolves_explicit_env_source(self):
        self.assertEqual(
            resolve_api_key(_provider(api_key='${env:AI_TEST_API_KEY}')),
            'env-secret',
        )

    @override_settings(AI_SETTINGS_ONLY='settings-secret')
    def test_env_source_does_not_fall_back_to_settings(self):
        self.assertIsNone(resolve_secret_reference('${env:AI_SETTINGS_ONLY}'))

    @mock.patch.dict('os.environ', {'AI_ENV_ONLY': 'env-secret'})
    def test_settings_source_does_not_fall_back_to_env(self):
        self.assertIsNone(resolve_secret_reference('${settings:AI_ENV_ONLY}'))

    @override_settings(AI_EMPTY_SECRET='', AI_NON_STRING_SECRET=123)
    def test_missing_empty_and_non_string_values_are_unresolved(self):
        self.assertIsNone(resolve_secret_reference('${settings:AI_MISSING_SECRET}'))
        self.assertIsNone(resolve_secret_reference('${settings:AI_EMPTY_SECRET}'))
        self.assertIsNone(resolve_secret_reference('${settings:AI_NON_STRING_SECRET}'))

    def test_invalid_reference_syntax_is_unresolved(self):
        for reference in (
            'literal-secret',
            'AI_TEST_API_KEY',
            '${AI_TEST_API_KEY}',
            '${vault:AI_TEST_API_KEY}',
            '${env:SECRET_KEY}',
            '${env:AI_lowercase}',
        ):
            with self.subTest(reference=reference):
                self.assertIsNone(resolve_secret_reference(reference))

    def test_omitted_api_key_remains_optional(self):
        self.assertIsNone(resolve_api_key(_provider()))


class ConfigFingerprintTest(SimpleTestCase):
    def test_stable_across_key_order_and_sensitive_to_content(self):
        config = _config()
        reordered = json.loads(json.dumps(config, sort_keys=True))
        self.assertEqual(config_fingerprint(config), config_fingerprint(reordered))
        changed = _config()
        changed['providers'][0]['api_url'] = 'http://localhost:10/v1'
        self.assertNotEqual(config_fingerprint(config), config_fingerprint(changed))


class ParseAiConfigTest(SimpleTestCase):
    def test_parses_current_shape(self):
        config = parse_ai_config(_config())
        self.assertEqual(config.providers[0].id, 'proxy')
        self.assertEqual(config.providers[0].models[0].id, 'test-model')

    def test_outdated_stored_config_degrades_to_empty(self):
        # A DB row written against the pre-models.dev schema (model 'name' as
        # the id) must not break every /chat endpoint after a deploy; it is
        # logged and treated as an empty provider set until re-authored.
        legacy = {'providers': [{'id': 'p', 'type': 'openai', 'models': [{'name': 'm'}]}]}
        config = parse_ai_config(legacy)
        self.assertEqual(config.providers, [])

    def test_legacy_api_key_field_is_not_silently_ignored(self):
        legacy = _config()
        provider = legacy['providers'][0]
        provider['api_key_' + 'env'] = 'AI_TEST_API_KEY'
        del provider['api_key']
        self.assertEqual(parse_ai_config(legacy).providers, [])

    def test_provider_rejects_invalid_api_key_references(self):
        for reference in (
            'literal-secret',
            'AI_TEST_API_KEY',
            '${AI_TEST_API_KEY}',
            '${vault:AI_TEST_API_KEY}',
            '${settings:SECRET_KEY}',
        ):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                _provider(api_key=reference)


class ReasoningEffortsTest(SimpleTestCase):
    def test_derived_from_reasoning_flag(self):
        reasoning = ModelEntry(id='m', reasoning=True)
        self.assertEqual(
            reasoning.reasoning_efforts,
            ['minimal', 'low', 'medium', 'high', 'xhigh'],
        )
        self.assertEqual(reasoning.default_reasoning_effort, 'medium')

        plain = ModelEntry(id='m')
        self.assertEqual(plain.reasoning_efforts, [])
        self.assertIsNone(plain.default_reasoning_effort)


class ResolveModelRequestTest(SimpleTestCase):
    def _cfg(self, **model_extra):
        return AiConfig.model_validate(_config(**model_extra))

    def test_resolves_and_defaults_effort(self):
        provider, model, effort = resolve_model_request(
            self._cfg(reasoning=True), 'proxy', 'test-model'
        )
        self.assertEqual(provider.id, 'proxy')
        self.assertEqual(model.id, 'test-model')
        self.assertEqual(effort, 'medium')  # normalized to model default

    def test_no_effort_for_non_reasoning_model(self):
        _p, _m, effort = resolve_model_request(self._cfg(), 'proxy', 'test-model')
        self.assertIsNone(effort)

    def test_unknown_provider_raises(self):
        with self.assertRaisesMessage(ModelRequestError, 'Unknown chat provider'):
            resolve_model_request(self._cfg(), 'nope', 'test-model')

    def test_unknown_model_raises(self):
        with self.assertRaisesMessage(ModelRequestError, 'Unknown model'):
            resolve_model_request(self._cfg(), 'proxy', 'nope')

    def test_unsupported_effort_raises(self):
        with self.assertRaisesMessage(ModelRequestError, 'Unsupported reasoning effort'):
            resolve_model_request(self._cfg(reasoning=True), 'proxy', 'test-model', 'bogus')

    def test_error_is_value_error(self):
        self.assertTrue(issubclass(ModelRequestError, ValueError))


class PublicModelsTest(SimpleTestCase):
    def test_strips_backend_fields_and_derives_reasoning_support(self):
        view = public_models(AiConfig.model_validate(_config(reasoning=True)))
        provider = view['providers'][0]
        self.assertNotIn('api_url', provider)
        self.assertNotIn('api_key', provider)
        model = provider['models'][0]
        self.assertTrue(model['supports_reasoning_effort'])
        self.assertEqual(
            model['reasoning_efforts'],
            ['minimal', 'low', 'medium', 'high', 'xhigh'],
        )
        self.assertEqual(model['default_reasoning_effort'], 'medium')

        plain = public_models(AiConfig.model_validate(_config()))
        self.assertFalse(plain['providers'][0]['models'][0]['supports_reasoning_effort'])

    def test_exposes_models_dev_fields_and_falls_back_to_ids(self):
        config = AiConfig.model_validate(
            _config(
                tool_call=True,
                reasoning=True,
                limit={'context': 128000, 'output': 4096},
                modalities={'input': ['text', 'image'], 'output': ['text']},
            ),
        )
        provider = public_models(config)['providers'][0]
        # No provider/model display name configured: the ids double as names.
        self.assertEqual(provider['name'], 'proxy')
        model = provider['models'][0]
        self.assertEqual(model['id'], 'test-model')
        self.assertEqual(model['name'], 'test-model')
        self.assertTrue(model['tool_call'])
        self.assertTrue(model['reasoning'])
        self.assertEqual(model['limit']['context'], 128000)
        self.assertEqual(model['limit']['output'], 4096)
        self.assertIn('image', model['modalities']['input'])


class BuildAgentTest(SimpleTestCase):
    def setUp(self):
        build_agent.cache_clear()

    def _build(self, config, provider='proxy', model='test-model', effort=None):
        with mock.patch('bublik.ai.config.get_raw_ai_config', return_value=config):
            return build_agent(provider, model, effort, config_fingerprint(config))

    def test_unknown_provider_and_model_raise_value_error(self):
        with self.assertRaisesMessage(ValueError, 'Unknown chat provider'):
            self._build(_config(), provider='nope')
        with self.assertRaisesMessage(ValueError, 'Unknown model'):
            self._build(_config(), model='nope')

    def test_configured_but_unresolved_api_key_raises_value_error(self):
        # Without this guard pydantic-ai silently sends an 'api-key-not-set'
        # placeholder to the custom api_url and all the user sees is the
        # gateway's 401.
        with self.assertRaisesMessage(ValueError, 'AI_TEST_API_KEY'):
            self._build(_config())

    @override_settings(AI_TEST_API_KEY='sekret')
    def test_builds_model_with_tokens_and_thinking_settings(self):
        config = _config(limit={'output': 1234}, reasoning=True)
        agent = self._build(config, effort='high')
        self.assertEqual(agent.model_settings['max_tokens'], 1234)
        self.assertEqual(agent.model_settings['thinking'], 'high')

    @override_settings(AI_TEST_API_KEY='sekret')
    def test_cache_is_keyed_by_config_fingerprint(self):
        config = _config()
        first = self._build(config)
        self.assertIs(first, self._build(config))
        changed = _config()
        changed['providers'][0]['api_url'] = 'http://localhost:10/v1'
        self.assertIsNot(first, self._build(changed))


class EnrichModelTest(SimpleTestCase):
    def test_known_model_fills_unset_fields_only(self):
        provider = _provider(id='openai', type='openai')
        entry = enrich_model(ModelEntry(id='gpt-4.1'), provider)
        self.assertNotEqual(entry.name, 'gpt-4.1')  # models.dev display name
        self.assertIsNotNone(entry.limit)
        self.assertTrue(entry.tool_call)

        explicit = enrich_model(
            ModelEntry(id='gpt-4.1', name='My GPT', tool_call=False),
            provider,
        )
        self.assertEqual(explicit.name, 'My GPT')
        self.assertFalse(explicit.tool_call)
        self.assertIsNotNone(explicit.limit)  # still enriched

    def test_unknown_model_defaults_name_to_id(self):
        entry = enrich_model(
            ModelEntry(id='no-such-model-xyz'),
            _provider(id='custom-gw'),
        )
        self.assertEqual(entry.name, 'no-such-model-xyz')
        self.assertIsNone(entry.limit)


class PopulateModelsTest(SimpleTestCase):
    def test_explicit_models_win_over_discovery(self):
        provider = _provider(
            api_url='http://localhost:9/v1',
            models=[{'id': 'only-this'}],
        )
        with mock.patch('bublik.ai.discovery.httpx.get') as mock_get:
            models = populate_models(provider, None)
        mock_get.assert_not_called()
        self.assertEqual([m.id for m in models], ['only-this'])

    @mock.patch('bublik.ai.discovery._fetch_gateway_models')
    def test_api_url_uses_http_discovery_even_for_known_provider_id(self, mock_fetch):
        # A custom gateway is authoritative for which models it serves, even
        # when its id collides with a models.dev provider id.
        mock_fetch.return_value = [{'id': 'gw-model'}]
        provider = _provider(id='openai', api_url='http://localhost:9/v1')
        models = populate_models(provider, None)
        mock_fetch.assert_called_once()
        self.assertEqual([m.id for m in models], ['gw-model'])

    @mock.patch('bublik.ai.discovery._fetch_gateway_models')
    def test_http_discovered_display_name_is_raw_id(self, mock_fetch):
        # models.dev knows gpt-4.1, but a gateway-served variant only shares
        # the id: metadata is enriched while the name stays the raw id.
        mock_fetch.return_value = [{'id': 'gpt-4.1'}, {'id': 'own', 'display_name': 'Own'}]
        models = populate_models(_provider(api_url='http://localhost:9/v1'), None)
        by_id = {m.id: m for m in models}
        self.assertEqual(by_id['gpt-4.1'].name, 'gpt-4.1')
        self.assertIsNotNone(by_id['gpt-4.1'].limit)
        self.assertEqual(by_id['own'].name, 'Own')

    def test_models_dev_provider_id_populates_catalogue(self):
        models = populate_models(_provider(id='openai', api_url=None), None)
        self.assertTrue(models)
        self.assertTrue(all(m.name for m in models))

    def test_unknown_provider_without_api_url_yields_empty(self):
        self.assertEqual(populate_models(_provider(id='custom-gw'), None), [])

    def test_effective_config_populates_all_providers(self):
        config = AiConfig.model_validate(
            {'providers': [{'id': 'anthropic', 'type': 'anthropic'}]},
        )
        effective = effective_ai_config(config)
        self.assertTrue(effective.providers[0].models)
        # The authored config is left untouched.
        self.assertIsNone(config.providers[0].models)


class ModelDiscoveryTest(SimpleTestCase):
    def test_anthropic_is_in_discoverable_types(self):
        self.assertIn('anthropic', _DISCOVERABLE_TYPES)

    @mock.patch('bublik.ai.discovery.httpx.get')
    @override_settings(AI_ANTHROPIC_KEY='sk-ant-test')
    def test_anthropic_discovery_sends_correct_headers_and_params(self, mock_get):
        mock_get.return_value.json.return_value = {'data': []}
        mock_get.return_value.raise_for_status.return_value = None

        provider = _provider(
            id='anthropic-test',
            type='anthropic',
            api_url='https://api.anthropic.com/v1',
            api_key='${settings:AI_ANTHROPIC_KEY}',
        )
        populate_models(provider, resolve_api_key(provider))

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], 'https://api.anthropic.com/v1/models')
        self.assertEqual(kwargs['headers']['x-api-key'], 'sk-ant-test')
        self.assertEqual(kwargs['headers']['anthropic-version'], '2023-06-01')
        self.assertEqual(kwargs['params'], {'limit': 1000})

    @mock.patch('bublik.ai.discovery.httpx.get')
    def test_anthropic_discovery_extracts_display_name(self, mock_get):
        mock_get.return_value.json.return_value = {
            'data': [
                {'id': 'claude-sonnet-4-6-20250514', 'display_name': 'Claude Sonnet 4.6'},
                {'id': 'claude-opus-4-7-20250701', 'display_name': 'Claude Opus 4.7'},
            ],
        }
        mock_get.return_value.raise_for_status.return_value = None

        models = populate_models(
            _provider(
                id='anthropic-display',
                type='anthropic',
                api_url='https://api.anthropic.com/v1',
            ),
            None,
        )

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].id, 'claude-opus-4-7-20250701')
        self.assertEqual(models[0].name, 'Claude Opus 4.7')
        self.assertEqual(models[1].id, 'claude-sonnet-4-6-20250514')
        self.assertEqual(models[1].name, 'Claude Sonnet 4.6')

    @mock.patch('bublik.ai.discovery.httpx.get')
    @mock.patch.dict('os.environ', {'AI_OPENAI_KEY': 'sk-test'})
    def test_openai_discovery_uses_env_reference_for_bearer_auth(self, mock_get):
        mock_get.return_value.json.return_value = {'data': [{'id': 'gpt-4o'}]}
        mock_get.return_value.raise_for_status.return_value = None

        provider = _provider(
            id='openai-test',
            api_url='https://api.openai.com/v1',
            api_key='${env:AI_OPENAI_KEY}',
        )
        models = populate_models(provider, resolve_api_key(provider))

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].id, 'gpt-4o')
        _args, kwargs = mock_get.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer sk-test')
        self.assertNotIn('anthropic-version', kwargs['headers'])
        self.assertEqual(kwargs['params'], {})


class ResolveMcpHeadersTest(SimpleTestCase):
    @override_settings(AI_GITHUB_AUTH_TOKEN='ghtok')
    def test_substitutes_embedded_settings_reference(self):
        server = McpServer(
            id='github',
            url='https://api.githubcopilot.com/mcp/',
            headers={'Authorization': 'Bearer ${settings:AI_GITHUB_AUTH_TOKEN}'},
        )
        self.assertEqual(
            resolve_mcp_headers(server),
            {'Authorization': 'Bearer ghtok'},
        )

    @override_settings(AI_SERVICE_KEY='xyz')
    @mock.patch.dict('os.environ', {'AI_SERVICE_SUFFIX': 'tail'})
    def test_multiple_references_and_static_value_pass_through(self):
        server = McpServer(
            id='svc',
            url='https://example.com/mcp',
            headers={
                'X-Api-Key': 'pre-${settings:AI_SERVICE_KEY}-${env:AI_SERVICE_SUFFIX}',
                'X-Tenant': 'acme',
            },
        )
        self.assertEqual(
            resolve_mcp_headers(server),
            {'X-Api-Key': 'pre-xyz-tail', 'X-Tenant': 'acme'},
        )

    def test_missing_env_var_skips_server(self):
        server = McpServer(
            id='github',
            url='https://example.com/mcp',
            headers={'Authorization': 'Bearer ${env:AI_MISSING_TOKEN}'},
        )
        self.assertIsNone(resolve_mcp_headers(server))

    def test_complete_invalid_reference_skips_server(self):
        for reference in (
            '${AI_TOKEN}',
            '${vault:AI_TOKEN}',
            '${settings:SECRET_KEY}',
            '${env:AI_bad}',
            '${env:AI_TOKEN{nested}',
        ):
            with self.subTest(reference=reference):
                server = McpServer(
                    id='x',
                    url='https://example.com/mcp',
                    headers={'Authorization': f'Bearer {reference}'},
                )
                self.assertIsNone(resolve_mcp_headers(server))

    def test_no_headers_resolves_to_empty(self):
        self.assertEqual(
            resolve_mcp_headers(McpServer(id='x', url='https://example.com/mcp')),
            {},
        )


class BuildMcpToolsetsTest(SimpleTestCase):
    @override_settings(AI_GITHUB_AUTH_TOKEN='ghtok')
    def test_drops_servers_with_unresolved_references(self):
        config = AiConfig.model_validate(
            {
                'providers': [],
                'mcp_servers': [
                    {
                        'id': 'github',
                        'url': 'https://api.githubcopilot.com/mcp/',
                        'headers': {'Authorization': 'Bearer ${settings:AI_GITHUB_AUTH_TOKEN}'},
                    },
                    {
                        'id': 'broken',
                        'url': 'https://example.com/mcp',
                        'headers': {'Authorization': 'Bearer ${env:AI_MISSING_TOKEN}'},
                    },
                ],
            },
        )
        toolsets = build_mcp_toolsets(config)
        self.assertEqual(len(toolsets), 1)
        self.assertEqual(toolsets[0].prefix, 'github')

    def test_no_mcp_servers_yields_empty(self):
        self.assertEqual(build_mcp_toolsets(AiConfig()), [])


class AiSchemaTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        schema_path = Path(bublik.data.__file__).parent / 'schemas' / 'ai.json'
        cls.schema = json.loads(schema_path.read_text())
        Draft7Validator.check_schema(cls.schema)
        cls.validator = Draft7Validator(cls.schema)

    def _is_valid(self, config):
        return not list(self.validator.iter_errors(config))

    def test_embedded_default_validates(self):
        self.assertTrue(self._is_valid(self.schema['default']))

    def test_accepts_models_dev_field_names(self):
        config = _config(
            name='Test Model',
            tool_call=True,
            reasoning=True,
            limit={'context': 128000, 'output': 4096},
            modalities={'input': ['text', 'image'], 'output': ['text']},
        )
        self.assertTrue(self._is_valid(config))

    def test_provider_does_not_require_api_url_or_models(self):
        for provider_type in ('anthropic', 'gateway/anthropic', 'openai'):
            config = _config()
            config['providers'][0]['type'] = provider_type
            del config['providers'][0]['api_url']
            del config['providers'][0]['models']
            self.assertTrue(self._is_valid(config), provider_type)

    def test_rejects_unknown_type(self):
        config = _config()
        config['providers'][0]['type'] = 'nonsense'
        self.assertFalse(self._is_valid(config))

    def test_api_key_requires_exact_source_qualified_ai_reference(self):
        for reference in ('${env:AI_KEY}', '${settings:AI_KEY}'):
            config = _config()
            config['providers'][0]['api_key'] = reference
            self.assertTrue(self._is_valid(config), reference)

        for reference in (
            'literal-secret',
            'AI_KEY',
            '${AI_KEY}',
            '${vault:AI_KEY}',
            '${env:SECRET_KEY}',
            'prefix-${env:AI_KEY}',
            '${env:AI_KEY}-suffix',
            '${env:AI_KEY}\n',
        ):
            with self.subTest(reference=reference):
                config = _config()
                config['providers'][0]['api_key'] = reference
                self.assertFalse(self._is_valid(config))

    def test_rejects_legacy_api_key_field(self):
        config = _config()
        del config['providers'][0]['api_key']
        config['providers'][0]['api_key_' + 'env'] = 'AI_TEST_API_KEY'
        self.assertFalse(self._is_valid(config))

    def test_rejects_retired_fields_and_types(self):
        self.assertFalse(self._is_valid(_config(display_name='Legacy Name')))
        self.assertFalse(self._is_valid(_config(reasoning_efforts=['low', 'high'])))
        self.assertFalse(self._is_valid(_config(default_reasoning_effort='high')))
        self.assertFalse(self._is_valid(_config(max_output_tokens=4096)))
        self.assertFalse(self._is_valid(_config(capabilities={'tools': True})))

        config = _config()
        config['providers'][0]['type'] = 'openai_compatible'
        self.assertFalse(self._is_valid(config))

        config = _config()
        config['providers'][0]['display_name'] = 'Legacy'
        self.assertFalse(self._is_valid(config))

        config = _config()
        config['providers'][0]['model_overrides'] = [{'id': 'test-model'}]
        self.assertFalse(self._is_valid(config))

    def test_model_requires_id(self):
        config = _config()
        config['providers'][0]['models'] = [{'name': 'No Id'}]
        self.assertFalse(self._is_valid(config))

    def test_valid_mcp_server_validates(self):
        config = _config()
        config['mcp_servers'] = [
            {
                'id': 'github',
                'url': 'https://api.githubcopilot.com/mcp/',
                'headers': {'Authorization': 'Bearer ${env:AI_GITHUB_AUTH_TOKEN}'},
            },
        ]
        self.assertTrue(self._is_valid(config))

    def test_mcp_server_requires_id_and_url(self):
        for missing in ('id', 'url'):
            config = _config()
            server = {'id': 'github', 'url': 'https://example.com/mcp'}
            del server[missing]
            config['mcp_servers'] = [server]
            self.assertFalse(self._is_valid(config), missing)

    def test_mcp_server_rejects_unknown_fields(self):
        config = _config()
        config['mcp_servers'] = [
            {'id': 'github', 'url': 'https://example.com/mcp', 'oauth': {}},
        ]
        self.assertFalse(self._is_valid(config))

    def test_config_without_mcp_servers_still_validates(self):
        self.assertTrue(self._is_valid(_config()))
