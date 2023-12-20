[SPDX-License-Identifier: Apache-2.0]::
[Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.]::

Here is the list of all per-project configurable places in Bublik.

# Per-project conception
All the **metas** which users can apply to customize their Bublik pages are came from *meta_data.json*.

**Tags** are special metas which are came from TE log and cannot be changed in Bublik.

# Dashboard

Dashboard table is fully configurable by the following settings in *per_conf.py*:
- **DASHBOARD_HEADER**

Sets columns on <u>dashboard</u>. Basically each column is values of metas from the category this column is dedicated to, except columns by keys: 'total', 'total_expected', 'progress', 'unexpected' which shows extended data Bublik generates itself.

Represents dictionary where the keys are common to all dashboard settings and users set it by themselves, the values - meta category names.

- **DASHBOARD_PAYLOAD**

  - 'go_run'         -> go to run details
  - 'go_run_failed'  -> go to run details with failed results opened
  - 'go_tree'        -> go to run tests as a tree with its logs and context
  - 'go_bug'         -> go to bug in the repository
  - 'go_source'      -> go to source from which the run was imported

Sets action on column cells click. Usually it moves to another view.

Represents dictionary where the keys defines the column from DASHBOARD_HEADER and the values available are describe above.


- **DASHBOARD_ROWS_STYLE**

Sets either dashboard rows should be colored or not. To have colorful rows set it to 'colored'. By default it's not set.

Works only for UI-V1 - in V2 we have the common style of rows coloring.

- **DASHBOARD_DATE**

Represents the name of the meta pointing to which date the run is related to (for cases when run starts one day and finishes the next day).

- **DASHBOARD_RUNS_SORT**

Sets the columns dashboard rows are sorted by default.

Represents a list of DASHBOARD_HEADER keys and extra 'start' key which defines run start.

- **DASHBOARD_DEFAULT_MODE**

Sets the mode which dashboard follows when opens.

Options are 'one_day_one_column', 'one_day_two_columns', 'two_days_two_columns'.

Works only for UI-V2.

# Metadata on pages

**Metadata** column on <u>history</u> and <u>runs</u> pages is managed by **METADATA_ON_PAGES** setting in *per_conf.py* which represents a list of meta category names.

# Special categories in Info on Run and Log pages
Extra per project data in **Info** block on <u>log</u> and <u>run</u> page are managed by **SPECIAL_CATEGORIES** setting in *per_conf.py* which represents a list of meta category names.

# File defining run testing was completed

The file indicating that run testing was completed is managed by **RUN_COMPLETE_FILE** setting in *per_conf.py* which represents the name of some file accessed by run source link.

# Run uniqueness

Bublik distinguishes one run from another by special set of metas defined in **RUN_KEY_METAS** setting in *per_conf.py* and represented a list of meta names.

# Applying changes

- If you change *meta.conf* or *tags.conf* you should send this HTTP request on the web:

  `GET: <bublik-web>/meta_categorization`

  Example: https://ts-factory.io/bublik/meta_categorization

- If you change *per_conf.py* you should restart the server:

  `./scripts/deploy --steps run_server`

- If you change *references.py* you should restart the server:

  `./scripts/deploy --steps run_server`

- If you change some <u>dashboard</u> settings in *meta.conf* or *per_conf.py* it's can be useful to drop cache from the web (just to click on the clock in the upper right corner).

**NB!** After all config changes you should reload web pages using `Ctrl + Shift + R` .

# To solve "CSRF verification failed" in POST requests on hosted Bublik instances.
# Thus we say Bublik server trust requests coming from CSRF_TRUSTED_ORIGINS.
# More details: https://docs.djangoproject.com/en/3.0/ref/settings/#csrf-trusted-origins
#
CSRF_TRUSTED_ORIGINS = ['ts-factory.io']
