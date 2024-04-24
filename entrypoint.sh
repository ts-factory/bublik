#!/bin/bash

echo "Collect static files"
python manage.py collectstatic --noinput

echo "Apply database migrations"
python manage.py makemigrations
python manage.py migrate

# echo "=============================================="
# echo "Ownership and Permissions of /app/bublik:"
# ls -l /app/bublik
# echo "=============================================="

exec "$@"