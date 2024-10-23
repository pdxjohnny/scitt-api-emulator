#!/usr/bin/env bash
# Usage: PYTHONPATH=$PWD:$PYTHONPATH PORT=8000 bash start.sh
set -xeuo pipefail

rm -rf workspace/ federation_workspace/

mkdir -pv workspace/storage/operations/ federation_workspace/

tee federation_workspace/config.json <<EOF
{
  "handle_name": "demo",
  "fqdn": "scitt.unstable.chadig.com",
  "workspace": "${PWD}/federation_workspace/",
  "bovine_db_url": "sqlite://${PWD}/federation_workspace/bovine.sqlite3",
  "following": {}
}
EOF

scitt-emulator server \
  --tree-alg CCF \
  --port "${PORT}" \
  --workspace workspace/ \
  --middleware \
    sphinx_docs_middleware:SphinxDocsMiddleware \
    scitt_emulator.federation_activitypub_bovine:SCITTFederationActivityPubBovine \
  --middleware-config-path \
    - \
    federation_workspace/config.json