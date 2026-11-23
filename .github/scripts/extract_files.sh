#!/bin/sh
set -eu

# GitHub withholds repository secrets from pull requests opened from forks
# and by Dependabot, so FILES_PASSPHRASE is empty there. The workflow sets
# FIXTURES_OPTIONAL=true only for those runs: skip extraction and let the
# e2e tests skip themselves (they skip when their env vars are empty).
# Anywhere else an empty passphrase is a misconfiguration, so fail rather
# than let the e2e suite go quiet.
if [ -z "${FILES_PASSPHRASE:-}" ]; then
    if [ "${FIXTURES_OPTIONAL:-false}" = "true" ]; then
        echo "::notice title=Fixtures skipped::Secrets are not available to this pull request; encrypted fixtures were not extracted and the e2e tests will be skipped."
        exit 0
    fi
    echo "::error title=Missing secret::FILES_PASSPHRASE is empty; cannot decrypt the test fixtures." >&2
    exit 1
fi

gpg -q --batch --yes -d --passphrase="$FILES_PASSPHRASE" \
    -o tests/files.tar.bz2 tests/files.enc
tar xjf tests/files.tar.bz2 -C tests/
