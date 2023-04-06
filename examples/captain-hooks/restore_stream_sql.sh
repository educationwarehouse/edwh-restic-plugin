#!/usr/bin/bash
restic $HOST -r $URI dump $SNAPSHOT --tag stream pg_dump.sql
