#!/bin/sh
# 시작할 때마다 DB 마이그레이션을 최신으로 맞춘 뒤 서버를 띄운다.
set -e
alembic upgrade head
exec "$@"
