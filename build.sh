#!/usr/bin/env bash
# build.sh — build and install the minigraf Python extension.
#
# maturin (bindings = "uniffi") calls `cargo run --bin uniffi-bindgen` to
# generate Python bindings. This repo contains a thin shim crate (Cargo.toml)
# whose sole purpose is to provide that binary via minigraf-ffi from crates.io.
#
# Requires an active virtualenv (or conda environment).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Pre-build the uniffi-bindgen binary from the shim crate.
cargo build --bin uniffi-bindgen

SUBCOMMAND="${1:-}"

if [[ "$SUBCOMMAND" == "build" ]]; then
    shift
    maturin build "$@"
else
    maturin develop
    [[ "$SUBCOMMAND" == "test" ]] && pytest tests/ -v
fi
