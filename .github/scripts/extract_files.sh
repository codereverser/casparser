#!/bin/sh

gpg -q --batch --yes -d --passphrase="$FILES_PASSPHRASE" \
    -o tests/files.tar test/files.enc
tar xf tests/files.tar -C tests/