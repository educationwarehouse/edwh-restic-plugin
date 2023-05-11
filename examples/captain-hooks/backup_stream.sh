#!/bin/bash
# run the pg_dump application from the migrate container and pipe the output to restic
# restic will use $HOST as the hostname when saving the backup, $URI for the backup uri itself.
# in this case the snapshot will be tagged with "stream" to mark a stream backup, and the
# --stdin --stdin-filename options are used to tell restic to read the backup from stdin as save it as postgres.sql

docker-compose run -T --rm migrate pg_dump --format=p --dbname=backend --clean --create -h pgpool -U postgres | restic $HOST -r $URI backup --tag stream --stdin --stdin-filename postgres.sql
