[SPDX-License-Identifier: Apache-2.0]::
[Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.]::

# Run report configuration

To build a run report, an appropriate configuration is required.

## Format

The configuration file must be in JSON format and include:

- 'id',
- 'name',
- 'description',
- 'version',
- 'title_content' (metadata list that will be used in the title),
- 'test_names_order' (test names list for tests sorting),
- 'tests' (test configurations list).

Test configuration must include:

- 'table_view' (flag),
- 'chart_view' (flag),
- 'axis_x' (test argument),
- 'axis_y' (measurement type with a list of measurement names (if you want specific ones) or with an empty list),
- 'sequence_group_arg' (test argument that will be used to group the measurement results in a sequences),
- 'percentage_base_value' (sequence group argument value, relative to which the percentages will be calculated),
- 'sequence_name_conversion' (sequence argument values with corresponding sequences names),
- 'not_show_args' (test arguments with their values. The results of the corresponding iterations will not be included in the report.),
- 'records_order' (test arguments list for reports sorting).

You can find an example of the run report configuration in *report_config.json*.

## Location

All possible run report configuration files should be stored in the *<PER_CONF_DIR>/reports* directory.
