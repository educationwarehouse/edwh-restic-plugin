#!/bin/bash
# recover the backup from the files snapshot and save it in ./

restic $HOST -r $URI restore $SNAPSHOT --tag files --target ./ --exclude .git
