# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.decorators.cache import cache_page
from rest_framework.routers import DefaultRouter

from bublik.core.cache import cache_page_if_run_done
from bublik.core.routers import ActionsOnlyRouter
from bublik.interfaces import api_v1, api_v2, main_api


### URL patterns for API-V1 ###
importruns_router = ActionsOnlyRouter()
importruns_router.register(r'', api_v1.ImportrunsViewSet, basename='importruns')

### URL patterns for API-V2 ###
api_v2_router = DefaultRouter()
api_v2_router.register(r'runs', api_v2.RunViewSet, 'runs')
api_v2_router.register(r'results', api_v2.ResultViewSet, 'results')
api_v2_router.register(r'logs', api_v2.LogViewSet, 'logs')
api_v2_router.register(r'tree', api_v2.TreeViewSet, 'tree')
api_v2_router.register(r'dashboard', api_v2.DashboardViewSet, 'dashboard')
api_v2_router.register(r'history', api_v2.HistoryViewSet, 'history')
api_v2_router.register(r'server', api_v2.ServerViewSet, 'server')
api_v2_router.register(r'outside_domains', api_v2.OutsideDomainsViewSet, 'outside_domains')
api_v2_router.register(r'measurements', api_v2.MeasurementViewSet, 'measurements')
api_v2_router.register(r'session_import', api_v2.EventLogViewSet, 'session_import')
api_v2_router.register(r'importruns', api_v2.ImportrunsViewSet, 'importruns')
api_v2_router.register(r'auth/profile', api_v2.ProfileViewSet, 'profile')
api_v2_router.register(r'auth/admin', api_v2.AdminViewSet, 'admin')
api_v2_router.register(r'report', api_v2.ReportViewSet, 'report')

### URL patterns mounting ###
urlpatterns = [
    # Built-in
    path('admin/', admin.site.urls),
    path('api-auth/', include('rest_framework.urls', namespace='rest_framework')),
    # Management commands web interface
    path('importruns/', include(importruns_router.urls)),
    path('meta_categorization/', api_v2.meta_categorization, name='meta_categorization'),
    path(
        'clear_all_runs_stats_cache/',
        api_v2.clear_all_runs_stats_cache,
        name='clear_all_runs_stats_cache',
    ),
    re_path(r'importlog/(?:(?P<task_id>[a-fA-F-\d]{36})?)$',
            cache_page(60 * 20)(api_v2.local_logs), name='logs'),
    # V2
    re_path(r'^v2/$', api_v2.render_react, name='ui-v2-index'),
    re_path(r'^v2/(?:.*)/?$', api_v2.render_react, name='ui-v2-routes'),
    path('api/v2/', include((api_v2_router.urls, 'api-v2'), namespace='api-v2')),
    # Redirects
    path('', main_api.redirect_root),
    re_path(r'short/(?P<view_endpoint_hash>[A-Za-z0-9+/=]+)$', main_api.redirect_short),
    path('dashboard/', main_api.redirect_dashboard),
    path('v1/runs_stats/', main_api.redirect_runs_stats),
    path('v1/flower/', main_api.redirect_flower, name='flower_site'),
    re_path(r'v1/run_stats/(?P<run_id>[0-9]+)$', main_api.redirect_run_stats),
    re_path(r'v1/tests_run/(?P<run_id>[0-9]+)$', main_api.redirect_tests_run),
    re_path(r'v1/result_log/(?:(?P<result_id>[0-9]+)?)$', main_api.redirect_result_log),
    re_path(r'next/(?:.*)/?$', main_api.redirect_next),
    path('auth/register/', api_v2.RegisterView.as_view(), name='auth_register'),
    path(
        'auth/register/activate/<str:user_id_b64>/<str:token>/',
        api_v2.ActivateView.as_view(),
        name='auth_register_activate',
    ),
    path('auth/login/', api_v2.LogInView.as_view(), name='auth_login'),
    path('auth/refresh/', api_v2.RefreshTokenView.as_view(), name='auth_refresh'),
    path('auth/logout/', api_v2.LogOutView.as_view(), name='auth_logout'),
    path(
        'auth/forgot_password/',
        api_v2.ForgotPasswordView.as_view(),
        name='auth_forgot_password',
    ),
    path(
        'auth/forgot_password/password_reset/<str:user_id_b64>/<str:token>/',
        api_v2.ForgotPasswordResetView.as_view(),
        name='auth_forgot_password_password_reset',
    ),
    path('performance_check/', api_v2.PerformanceCheckView.as_view(), name='performance_check'),
    path('url_shortner/', api_v2.URLShortnerView.as_view(), name='url_shortner'),
]

if settings.URL_PREFIX:
    urlpatterns = [re_path(f'^{settings.URL_PREFIX}/', include(urlpatterns))]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns = [path('__debug__/', include(debug_toolbar.urls)), *urlpatterns]
