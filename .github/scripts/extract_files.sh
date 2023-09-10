#!/bin/sh

gpg -q --batch --yes -d --passphrase="$FILES_PASSPHRASE" \
    -o tests/files.tar.bz2 tests/files.enc
tar xjf tests/files.tar.bz2 -C tests/
