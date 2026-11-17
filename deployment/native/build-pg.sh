#!/usr/bin/env bash
# A relocatable PostgreSQL for the native bundle: OpenSSL (LTS) built from source into <out>/lib, PostgreSQL without ICU (initdb
# uses the builtin locale provider) or readline (no psql editing), pgvector and pg_textsearch against that tree. Libraries are
# found relative to each file ($ORIGIN on Linux, @loader_path on macOS), so the folder can move.
#   build-pg.sh <out-dir>        needs a C compiler, make, perl, flex and bison (PostgreSQL 18 requires both); Linux CI runs it in manylinux_2_28 (glibc 2.28)
. "$(dirname "$0")/lib.sh"
out=$(abspath "$1"); rm -rf "${out:?}"/*
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
cd "$work"
tar xzf "$(fetch "https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz" "$OPENSSL_SHA256" "openssl-$OPENSSL_VERSION.tar.gz")"
tar xjf "$(fetch "https://ftp.postgresql.org/pub/source/v$PG_VERSION/postgresql-$PG_VERSION.tar.bz2" "$PG_SHA256" "postgresql-$PG_VERSION.tar.bz2")"
tar xzf "$(fetch "https://github.com/pgvector/pgvector/archive/refs/tags/v$PGVECTOR_VERSION.tar.gz" "$PGVECTOR_SHA256" "pgvector-$PGVECTOR_VERSION.tar.gz")"
tar xzf "$(fetch "https://github.com/timescale/pg_textsearch/archive/refs/tags/v$PG_TEXTSEARCH_VERSION.tar.gz" "$PG_TEXTSEARCH_SHA256" "pg_textsearch-$PG_TEXTSEARCH_VERSION.tar.gz")"

# OpenSSL: shared libraries only (install_sw: no docs, no config files), into the bundle's lib/.
(cd "openssl-$OPENSSL_VERSION" && ./Configure --prefix="$out" --libdir=lib --openssldir="$out/ssl" shared no-tests no-docs \
  && make -s -j"$jobs" && make -s install_sw) >/dev/null

# --disable-rpath: PostgreSQL would otherwise record the absolute libdir; the relative paths are set below. Until then the build's
# own test programs find OpenSSL through the loader path, which the self-check at the end clears again.
export LD_LIBRARY_PATH="$out/lib" DYLD_LIBRARY_PATH="$out/lib"
(cd "postgresql-$PG_VERSION" && ./configure -q --prefix="$out" --with-ssl=openssl --without-icu --without-readline \
  --disable-rpath CPPFLAGS="-I$out/include" LDFLAGS="-L$out/lib" \
  && make -s -j"$jobs" world-bin && make -s install-world-bin) >/dev/null
export PATH="$out/bin:$PATH"
for d in "pgvector-$PGVECTOR_VERSION" "pg_textsearch-$PG_TEXTSEARCH_VERSION"; do
  make -s -C "$d" -j"$jobs" PG_CONFIG="$out/bin/pg_config" with_llvm=no >/dev/null
  make -s -C "$d" PG_CONFIG="$out/bin/pg_config" with_llvm=no install >/dev/null
done

# What a server never needs: headers, static libraries, OpenSSL's command line and config, the client-only tools.
rm -rf "$out/include" "$out/ssl" "$out/share/doc" "$out/share/man" "$out/lib/pkgconfig" "$out/lib/postgresql/pgxs" "$out"/lib/*.a "$out/bin/openssl" "$out/bin/c_rehash"
for b in "$out"/bin/*; do case $(basename "$b") in initdb|pg_ctl|postgres|pg_config|pg_dump|pg_restore|psql|pg_isready) ;; *) rm -f "$b";; esac; done

if [ "$os" = linux ]; then
  for f in "$out"/bin/*; do patchelf --set-rpath '$ORIGIN/../lib' "$f" 2>/dev/null || true; done
  for f in "$out"/lib/*.so*; do [ -L "$f" ] || patchelf --set-rpath '$ORIGIN' "$f"; done
  for f in "$out"/lib/postgresql/*.so; do patchelf --set-rpath '$ORIGIN/..' "$f"; done
else
  # Every reference to <out>/lib/libX becomes @rpath/libX, each library is known by that name, and each file's rpath points at lib/.
  fix() {
    local f=$1 rp=$2 dep
    for dep in $(otool -L "$f" | awk 'NR>1 {print $1}' | grep "^$out/lib/" || true); do
      install_name_tool -change "$dep" "@rpath/$(basename "$dep")" "$f"
    done
    case $f in *.dylib) install_name_tool -id "@rpath/$(basename "$f")" "$f";; esac
    install_name_tool -add_rpath "$rp" "$f" 2>/dev/null || true
  }
  for f in "$out"/bin/*; do [ "$(basename "$f")" = pg_config ] || fix "$f" @loader_path/../lib; done
  for f in "$out"/lib/*.dylib; do [ -L "$f" ] || fix "$f" @loader_path; done
  for f in "$out"/lib/postgresql/*.dylib "$out"/lib/postgresql/*.so; do [ -e "$f" ] && fix "$f" @loader_path/..; done
fi

# Self-check from a copy in another folder: the libraries resolve relative to the files, and every extension the schema uses loads.
unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH
moved="$work/moved"; cp -R "$out" "$moved"
t="$work/check"; port=${CHECK_PORT:-54990}
"$moved/bin/initdb" -D "$t" -U postgres --auth=trust -E UTF8 --locale=C --locale-provider=builtin --builtin-locale=C.UTF-8 >/dev/null
"$moved/bin/pg_ctl" -D "$t" -l "$work/check.log" -w -o "-p $port -c listen_addresses=127.0.0.1 -k $work -c shared_preload_libraries=pg_stat_statements,auto_explain,pg_textsearch" start >/dev/null
ok=$("$moved/bin/psql" -h 127.0.0.1 -p "$port" -U postgres -d postgres -Atq -c "CREATE EXTENSION vector; CREATE EXTENSION pg_trgm; CREATE EXTENSION pg_textsearch; CREATE EXTENSION pg_stat_statements;" \
  -c "SELECT string_agg(extname||'='||extversion, ' ' ORDER BY extname) FROM pg_extension" -c "SHOW ssl_library" 2>&1) || ok="$ok (failed)"
"$moved/bin/pg_ctl" -D "$t" -m fast -w stop >/dev/null
echo "pg: $("$moved/bin/postgres" --version); extensions: $ok"
case $ok in *pg_textsearch=*vector=*OpenSSL*) ;; *) echo "build-pg: self-check failed" >&2; cat "$work/check.log" >&2; exit 1;; esac
du -sh "$out"
