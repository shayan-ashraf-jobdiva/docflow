#!/bin/bash
# EC2 "User data": runs once at first boot (Amazon Linux 2023).
# Installs what the app needs and registers it as a service. The app itself
# is installed later by the pipeline (deploy/deploy.sh), straight from S3.
set -euxo pipefail

dnf install -y python3 unzip

id docflow >/dev/null 2>&1 || useradd --system --home-dir /opt/docflow --shell /sbin/nologin docflow
mkdir -p /opt/docflow/releases /var/lib/docflow
chown -R docflow:docflow /opt/docflow /var/lib/docflow

cat > /etc/systemd/system/docflow.service <<'UNIT'
[Unit]
Description=DocFlow
After=network.target

[Service]
User=docflow
WorkingDirectory=/opt/docflow/current
Environment=DOCFLOW_DATA=/var/lib/docflow
ExecStart=/opt/docflow/current/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 80
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable docflow
