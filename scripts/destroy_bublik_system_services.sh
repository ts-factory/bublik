#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

set -ex

# Stop nginx which uses bublik-gunicorn.service
sudo systemctl stop nginx

# Remove system bublik services from auto-start after a system reboot
sudo systemctl disable flower.service
sudo systemctl disable celery.service
sudo systemctl disable bublik-gunicorn.socket
sudo systemctl disable bublik-gunicorn.service
sudo systemctl disable bublik-logs-ticket.service

# Stop all bublik system services
sudo systemctl stop flower.service
sudo systemctl stop celery.service
sudo systemctl stop bublik-gunicorn.socket
sudo systemctl stop bublik-gunicorn.service
sudo systemctl stop bublik-logs-ticket.service

# Remove all bublik system services
sudo rm -f /etc/systemd/system/flower.service
sudo rm -f /etc/systemd/system/celery.service
sudo rm -f /etc/systemd/system/bublik-gunicorn.socket
sudo rm -f /etc/systemd/system/bublik-gunicorn.service
sudo rm -f /etc/systemd/system/bublik-logs-ticket.service
