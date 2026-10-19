#!/bin/sh
# Installs bedrock-protocol into bot/node_modules for the companion bodies (which run in the node:20 image).
# Its RakNet transport is a native module built from source, which needs cmake: this does it all in Docker.
cd "$(dirname "$0")" && docker run --rm --platform linux/amd64 -v "$PWD:/bot" -w /bot node:20 \
  sh -c "apt-get update -qq && apt-get install -y -qq cmake >/dev/null && npm install"
