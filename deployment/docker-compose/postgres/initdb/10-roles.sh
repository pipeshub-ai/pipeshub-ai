#!/bin/bash
# First start only (docker-entrypoint-initdb.d): the same role setup that runs on every start.
exec /usr/local/bin/pg-roles.sh
