# SPDX-License-Identifier: Apache-2.0
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('data', '0013_issueext_issue_issuerule_resultclassification'),
    ]

    operations = [
        migrations.AlterField(
            model_name='issuerule',
            name='expected',
            field=models.BooleanField(
                null=True,
                blank=True,
                help_text='Disposition: True=expected (suppresses), False=unexpected, '
                'None=none (marker only).',
            ),
        ),
    ]
