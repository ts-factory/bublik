# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

import inspect
import json
import os
from pathlib import Path
from unittest import mock
import uuid

from django.core.cache import caches
from django.test import SimpleTestCase, override_settings
from jsonschema import Draft7Validator
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.openai import OpenAIProvider

from bublik.ai.agent import (
    _URL_PARAMS,
    TYPE_ALIASES,
    _infer_provider_model,
    _make_provider_factory,
    build_agent,
)
from bublik.ai.config import (
    DISCOVERY_SESSION_ID,
    ModelRequestError,
    config_fingerprint,
    effective_ai_config,
    parse_ai_config,
    public_models,
    resolve_api_key,
    resolve_headers,
    resolve_model_request,
    resolve_provider_headers,
    resolve_secret_reference,
)
from bublik.ai.discovery import (
    _DISCOVERABLE_TYPES,
    enrich_model,
    populate_models,
)
from bublik.ai.mcp import build_mcp_toolsets
from bublik.ai.types import AiConfig, McpServer, ModelEntry, Provider
import bublik.data


_DISCOVERY_TEST_CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
    'ai_models': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'ai-model-discovery-tests',
    },
}


_API_URL = 'http://localhost:9/v1'
# Types retired from the schema enum because their pydantic-ai class has a
# fixed endpoint; such vendors are configured as type 'openai' + api_url.
_FIXED_ENDPOINT_TYPES = (
    'openrouter',
    'cohere',
    'xai',
    'deepseek',
    'vercel',
    'together',
    'fireworks',
    'cerebras',
    'moonshotai',
    'github',
    'nebius',
    'ovhcloud',
)


def _config(**model_extra):
    return {
        'providers': [
            {
                'id': 'proxy',
                'type': 'openai',
                'api_url': _API_URL,
                'api_key': '${settings:AI_TEST_API_KEY}',
                'models': [{'id': 'test-model', **model_extra}],
            },
        ],
        'default_model': {'provider': 'proxy', 'model': 'test-model'},
    }


def _provider(**extra):
    return Provider.model_validate(
        {'id': 'proxy', 'type': 'openai', 'api_url': _API_URL, **extra},
    )


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

    def test_provider_requires_api_url(self):
        with self.assertRaises(ValueError):
            Provider.model_validate({'id': 'x', 'type': 'openai'})

    def test_provider_rejects_empty_or_scheme_less_api_url(self):
        # An empty base_url would let the SDKs fall back to env vars again.
        for api_url in ('', 'gw.test/v1', 'ftp://gw.test'):
            with self.subTest(api_url=api_url), self.assertRaises(ValueError):
                _provider(api_url=api_url)

    def test_provider_rejects_retired_types_at_parse_time(self):
        # The schema enum only guards saves; a stored config with a type
        # whose class has a fixed endpoint must not list models it cannot serve.
        for provider_type in ('openrouter', 'deepseek', 'nonsense'):
            with self.subTest(type=provider_type), self.assertRaises(ValueError):
                _provider(type=provider_type)
        raw = _config()
        raw['providers'][0]['type'] = 'openrouter'
        with self.assertLogs('bublik.ai.config', 'ERROR') as logs:
            self.assertEqual(parse_ai_config(raw).providers, [])
        self.assertIn('openrouter', logs.output[0])

    def test_provider_without_api_url_degrades_to_empty_with_log(self):
        # A config seeded before api_url became mandatory: chat has no
        # providers, and the log tells the operator what to fix.
        raw = {'providers': [{'id': 'openai', 'type': 'openai'}]}
        with self.assertLogs('bublik.ai.config', 'ERROR') as logs:
            self.assertEqual(parse_ai_config(raw).providers, [])
        self.assertIn('api_url', logs.output[0])

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


class ProviderFactoryTest(SimpleTestCase):
    """`_make_provider_factory` lets the endpoint come from nowhere but api_url."""

    URL = 'https://gw.test/v1'

    def _factory(self, provider_type, api_url=URL):
        provider = _provider(type=provider_type, api_url=api_url)
        return _make_provider_factory(provider, 'sekret')

    @mock.patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://evil.test/v1'})
    def test_openai_uses_api_url_as_base_url(self):
        provider = self._factory('openai')('openai-chat')
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.base_url.rstrip('/'), self.URL)

    def test_anthropic_uses_api_url_as_base_url(self):
        provider = self._factory('anthropic')('anthropic')
        self.assertIsInstance(provider, AnthropicProvider)
        self.assertEqual(provider.base_url.rstrip('/'), self.URL)

    def test_azure_passes_api_url_as_azure_endpoint(self):
        # A /v1 endpoint needs no api_version, so the real class constructs.
        endpoint = 'https://res.openai.azure.com/openai/v1'
        provider = self._factory('azure', endpoint)('azure')
        self.assertIsInstance(provider, AzureProvider)
        self.assertEqual(provider.base_url.rstrip('/'), endpoint)

    @mock.patch('bublik.ai.agent.gateway_provider')
    def test_gateway_forwards_api_key_and_base_url(self, mock_gateway):
        self._factory('gateway/anthropic')('gateway/anthropic')
        mock_gateway.assert_called_once_with(
            'anthropic',
            api_key='sekret',
            base_url=self.URL,
        )

    def test_type_with_fixed_endpoint_is_rejected(self):
        # Safety net behind the parse-time check: a retired type that somehow
        # reaches the factory (model_construct skips validation) is refused
        # instead of silently using the class's built-in URL.
        provider = Provider.model_construct(id='proxy', type='deepseek', api_url=self.URL)
        factory = _make_provider_factory(provider, 'sekret')
        with self.assertRaisesMessage(ValueError, "'deepseek'"):
            factory('deepseek')

    def test_remote_endpoint_without_api_key_is_warned_about(self):
        # The OpenAI SDK no longer fails fast without a key once base_url is
        # explicit; the operator gets told before the first 401.
        provider = _provider(api_url='https://gw.test/v1')
        with self.assertLogs('bublik.ai.agent', 'WARNING') as logs:
            _infer_provider_model(provider, 'proxy', 'm')
        self.assertIn('no api_key', logs.output[0])

    def test_local_endpoint_without_api_key_is_not_warned_about(self):
        provider = _provider(api_url='http://localhost:4000/v1')
        with self.assertNoLogs('bublik.ai.agent', 'WARNING'):
            _infer_provider_model(provider, 'proxy', 'm')


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


@override_settings(CACHES=_DISCOVERY_TEST_CACHES)
class PopulateModelsTest(SimpleTestCase):
    def setUp(self):
        caches['ai_models'].clear()

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
        # A non-discoverable type never touches the network: the catalogue
        # comes from the models.dev snapshot keyed on the provider id.
        provider = _provider(
            id='google',
            type='google',
            api_url='https://generativelanguage.googleapis.com',
        )
        with mock.patch('bublik.ai.discovery.httpx.get') as mock_get:
            models = populate_models(provider, None)
        mock_get.assert_not_called()
        self.assertTrue(models)
        self.assertTrue(all(m.name for m in models))

    @mock.patch('bublik.ai.discovery._fetch_gateway_models', return_value=[])
    def test_empty_discovery_stays_empty_even_for_known_provider_id(self, mock_fetch):
        # Endpoint down or /models behind auth the config does not carry: the
        # gateway is authoritative, so an id that collides with a models.dev
        # provider must not substitute that vendor's real catalogue.
        self.assertEqual(populate_models(_provider(id='openai'), None), [])
        self.assertEqual(populate_models(_provider(id='custom-gw'), None), [])
        self.assertEqual(mock_fetch.call_count, 2)

    def test_unknown_provider_id_on_non_discoverable_type_yields_empty(self):
        provider = _provider(id='custom-gw', type='google')
        self.assertEqual(populate_models(provider, None), [])

    @mock.patch('bublik.ai.discovery._fetch_gateway_models')
    def test_effective_config_populates_all_providers(self, mock_fetch):
        mock_fetch.return_value = [{'id': 'claude-sonnet-4-6'}]
        config = AiConfig.model_validate(
            {
                'providers': [
                    {
                        'id': 'anthropic',
                        'type': 'anthropic',
                        'api_url': 'https://api.anthropic.com',
                    }
                ]
            },
        )
        effective = effective_ai_config(config)
        self.assertTrue(effective.providers[0].models)
        # The authored config is left untouched.
        self.assertIsNone(config.providers[0].models)


@override_settings(CACHES=_DISCOVERY_TEST_CACHES)
class ModelDiscoveryTest(SimpleTestCase):
    def setUp(self):
        caches['ai_models'].clear()

    def test_anthropic_is_in_discoverable_types(self):
        self.assertIn('anthropic', _DISCOVERABLE_TYPES)
        self.assertNotIn('openrouter', _DISCOVERABLE_TYPES)

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
    def test_gateway_discovery_uses_dedicated_cache(self, mock_get):
        mock_get.return_value.json.return_value = {'data': [{'id': 'cached-model'}]}
        mock_get.return_value.raise_for_status.return_value = None
        provider = _provider(api_url='https://example.test/v1')

        first = populate_models(provider, None)
        second = populate_models(provider, None)

        self.assertEqual(first, second)
        mock_get.assert_called_once()

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

    @mock.patch('bublik.ai.discovery.httpx.get')
    @override_settings(AI_OPENAI_KEY='sk-test')
    def test_configured_header_overrides_the_derived_bearer_auth(self, mock_get):
        # Same precedence as the inference path (see `test_ai_chat_app`): a
        # gateway whose auth scheme `api_key` cannot express must be reachable
        # for discovery too, or none of its models show up in /chat/models.
        mock_get.return_value.json.return_value = {'data': []}
        mock_get.return_value.raise_for_status.return_value = None

        provider = _provider(
            id='openai-basic',
            api_url='https://gateway.test/v1',
            api_key='${settings:AI_OPENAI_KEY}',
            headers={'Authorization': 'Basic ZGVtbzpzM2tyZXQ='},
        )
        populate_models(
            provider,
            resolve_api_key(provider),
            resolve_provider_headers(provider, DISCOVERY_SESSION_ID),
        )

        _args, kwargs = mock_get.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Basic ZGVtbzpzM2tyZXQ=')

    @mock.patch('bublik.ai.discovery.httpx.get')
    @override_settings(AI_ANTHROPIC_KEY='sk-ant-test')
    def test_configured_header_overrides_the_derived_anthropic_key(self, mock_get):
        mock_get.return_value.json.return_value = {'data': []}
        mock_get.return_value.raise_for_status.return_value = None

        provider = _provider(
            id='anthropic-override',
            type='anthropic',
            api_url='https://gateway.test/v1',
            api_key='${settings:AI_ANTHROPIC_KEY}',
            headers={'x-api-key': 'gateway-issued', 'anthropic-version': '2024-01-01'},
        )
        populate_models(
            provider,
            resolve_api_key(provider),
            resolve_provider_headers(provider, DISCOVERY_SESSION_ID),
        )

        _args, kwargs = mock_get.call_args
        self.assertEqual(kwargs['headers']['x-api-key'], 'gateway-issued')
        self.assertEqual(kwargs['headers']['anthropic-version'], '2024-01-01')


class ResolveMcpHeadersTest(SimpleTestCase):
    @override_settings(AI_GITHUB_AUTH_TOKEN='ghtok')
    def test_substitutes_embedded_settings_reference(self):
        server = McpServer(
            id='github',
            url='https://api.githubcopilot.com/mcp/',
            headers={'Authorization': 'Bearer ${settings:AI_GITHUB_AUTH_TOKEN}'},
        )
        self.assertEqual(
            resolve_headers(server.headers, 'test'),
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
            resolve_headers(server.headers, 'test'),
            {'X-Api-Key': 'pre-xyz-tail', 'X-Tenant': 'acme'},
        )

    def test_missing_env_var_skips_server(self):
        server = McpServer(
            id='github',
            url='https://example.com/mcp',
            headers={'Authorization': 'Bearer ${env:AI_MISSING_TOKEN}'},
        )
        self.assertIsNone(resolve_headers(server.headers, 'test'))

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
                self.assertIsNone(resolve_headers(server.headers, 'test'))

    def test_no_headers_resolves_to_empty(self):
        self.assertEqual(
            resolve_headers(McpServer(id='x', url='https://example.com/mcp').headers, 'test'),
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

    def test_provider_requires_api_url(self):
        # No implicit endpoints: not even the native providers get one.
        for provider_type in ('anthropic', 'gateway/anthropic', 'openai'):
            config = _config()
            config['providers'][0]['type'] = provider_type
            del config['providers'][0]['api_url']
            self.assertFalse(self._is_valid(config), provider_type)

    def test_provider_does_not_require_models(self):
        config = _config()
        del config['providers'][0]['models']
        self.assertTrue(self._is_valid(config))

    @mock.patch('bublik.ai.discovery._fetch_gateway_models', return_value=[])
    def test_embedded_default_is_empty(self, mock_fetch):
        # Nothing in the deploy starts a gateway or local runtime, so seeded
        # entries would look like a working setup when they are not.
        self.assertEqual(self.schema['default'], {'providers': []})
        effective = effective_ai_config(parse_ai_config(self.schema['default']))
        self.assertEqual(effective.providers, [])
        mock_fetch.assert_not_called()

    def test_schema_rejects_empty_or_scheme_less_api_url(self):
        for api_url in ('', 'gw.test/v1', 'ftp://gw.test'):
            config = _config()
            config['providers'][0]['api_url'] = api_url
            self.assertFalse(self._is_valid(config), api_url)

    def test_every_enum_type_accepts_an_endpoint(self):
        # Ties the schema enum to the runtime rule in `_make_provider_factory`:
        # a pydantic-ai bump that fixes or frees a class's endpoint must be
        # reflected in the enum, not discovered at chat time.
        enum = self.schema['properties']['providers']['items']['properties']['type']['enum']
        for provider_type in enum:
            if provider_type.startswith('gateway/'):
                continue
            with self.subTest(type=provider_type):
                try:
                    cls = infer_provider_class(TYPE_ALIASES.get(provider_type, provider_type))
                except ImportError:
                    self.skipTest('optional provider package not installed')
                params = inspect.signature(cls.__init__).parameters
                self.assertTrue(any(name in params for name in _URL_PARAMS), cls.__name__)

    def test_fixed_endpoint_types_are_not_in_the_enum(self):
        enum = self.schema['properties']['providers']['items']['properties']['type']['enum']
        for provider_type in _FIXED_ENDPOINT_TYPES:
            with self.subTest(type=provider_type):
                self.assertNotIn(provider_type, enum)
                try:
                    cls = infer_provider_class(provider_type)
                except ImportError:
                    self.skipTest('optional provider package not installed')
                params = inspect.signature(cls.__init__).parameters
                self.assertFalse(any(name in params for name in _URL_PARAMS), cls.__name__)

    def test_rejects_unknown_and_fixed_endpoint_types(self):
        # Types whose pydantic-ai class has a fixed endpoint are gone from the
        # enum; such vendors are reached via type 'openai' + their api_url.
        for provider_type in ('nonsense', 'openrouter', 'deepseek', 'xai', 'together'):
            config = _config()
            config['providers'][0]['type'] = provider_type
            self.assertFalse(self._is_valid(config), provider_type)

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


class ProviderHeadersTest(SimpleTestCase):
    """Per-provider request headers (see `resolve_provider_headers`)."""

    THREAD = 'f0e0d3a2-1111-2222-3333-444455556666'

    def _provider(self, **headers):
        return Provider(id='opencode', type='openai', api_url=_API_URL, headers=headers)

    @override_settings(AI_TEST_API_KEY='sekret')
    def test_resolves_secrets_thread_id_and_static_values(self):
        provider = self._provider(
            **{
                'x-opencode-session': '${thread_id}',
                'Authorization': 'Bearer ${settings:AI_TEST_API_KEY}',
                'User-Agent': 'bublik-chat/1.0',
            }
        )
        self.assertEqual(
            resolve_provider_headers(provider, self.THREAD),
            {
                'x-opencode-session': self.THREAD,
                'Authorization': 'Bearer sekret',
                'User-Agent': 'bublik-chat/1.0',
            },
        )

    def test_thread_id_is_stable_per_conversation_and_differs_between_threads(self):
        provider = self._provider(**{'x-opencode-session': '${thread_id}'})
        first = resolve_provider_headers(provider, self.THREAD)
        # Prompt caching upstream depends on this being identical across turns.
        self.assertEqual(first, resolve_provider_headers(provider, self.THREAD))
        other = resolve_provider_headers(provider, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        self.assertNotEqual(first, other)

    def test_discovery_session_id_is_a_stable_uuid(self):
        provider = self._provider(**{'x-opencode-session': '${thread_id}'})
        resolved = resolve_provider_headers(provider, DISCOVERY_SESSION_ID)
        self.assertEqual(resolved['x-opencode-session'], DISCOVERY_SESSION_ID)
        self.assertEqual(str(uuid.UUID(DISCOVERY_SESSION_ID)), DISCOVERY_SESSION_ID)

    def test_thread_id_without_a_conversation_is_unresolved(self):
        provider = self._provider(**{'x-opencode-session': '${thread_id}'})
        self.assertEqual(resolve_provider_headers(provider, None), {})

    def test_no_headers_configured(self):
        provider = Provider(id='p', type='openai', api_url=_API_URL)
        self.assertEqual(resolve_provider_headers(provider), {})

    def test_non_ai_settings_name_is_refused(self):
        # The whole point of the AI_ guard: no reaching into arbitrary settings.
        self.assertIsNone(resolve_headers({'X': '${settings:SECRET_KEY}'}, 'test'))
        self.assertIsNone(resolve_headers({'X': '${env:PATH}'}, 'test'))

    @override_settings(AI_INJECTED='one\r\nX-Evil: two')
    def test_newline_in_a_resolved_value_is_refused(self):
        self.assertIsNone(resolve_headers({'X': '${settings:AI_INJECTED}'}, 'test'))

    @override_settings(AI_TEST_API_KEY='sekret')
    def test_headers_are_never_exposed_to_the_ui(self):
        config = AiConfig(
            providers=[
                Provider(
                    id='opencode',
                    type='openai',
                    api_url=_API_URL,
                    headers={'Authorization': 'Bearer ${settings:AI_TEST_API_KEY}'},
                    models=[ModelEntry(id='m')],
                )
            ]
        )
        payload = json.dumps(public_models(config))
        self.assertNotIn('headers', payload)
        self.assertNotIn('sekret', payload)
        self.assertNotIn('Authorization', payload)


class ProviderModelSettingsTest(SimpleTestCase):
    """Per-provider Pydantic AI model settings."""

    def test_defaults_to_empty(self):
        provider = Provider(id='p', type='openai', api_url=_API_URL)
        self.assertEqual(provider.model_settings, {})

    def test_round_trips_through_the_config(self):
        config = parse_ai_config(
            {
                'providers': [
                    {
                        'id': 'opencode-go',
                        'type': 'openai',
                        'api_url': _API_URL,
                        'model_settings': {'openai_continuous_usage_stats': True},
                    }
                ]
            }
        )
        self.assertEqual(
            config.providers[0].model_settings,
            {'openai_continuous_usage_stats': True},
        )

    def test_is_never_exposed_to_the_ui(self):
        config = AiConfig(
            providers=[
                Provider(
                    id='p',
                    type='openai',
                    api_url=_API_URL,
                    model_settings={'openai_continuous_usage_stats': True},
                    models=[ModelEntry(id='m')],
                )
            ]
        )
        self.assertNotIn('model_settings', json.dumps(public_models(config)))

    def test_schema_accepts_it(self):
        schema = json.loads(
            (Path(bublik.data.__file__).parent / 'schemas' / 'ai.json').read_text()
        )
        Draft7Validator(schema).validate(
            {
                'providers': [
                    {
                        'id': 'opencode-go',
                        'type': 'openai',
                        'api_url': _API_URL,
                        'model_settings': {'openai_continuous_usage_stats': True},
                    }
                ]
            }
        )
