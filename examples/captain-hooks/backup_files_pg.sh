#!/usr/bin/bash
# restic -r b2:edwh-backup-test:backup-testapplication init
restic $HOST -r $URI backup --tag files *.sh