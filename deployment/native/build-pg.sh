#!/usr/bin/env bash
# A relocatable PostgreSQL for the native bundle: OpenSSL (LTS) built from source into <out>/lib, PostgreSQL without ICU (initdb
# uses the builtin locale provider) or readline (no psql editing), pgvector and pg_textsearch against that tree. Libraries are
# found relative to each file ($ORIGIN on Linux, @loader_path on macOS), so the folder can move.
#   build-pg.sh <out-dir>        needs a C compiler, make, perl, flex and bison (PostgreSQL 18 requires both); Linux CI runs it in manylinux_2_28 (glibc 2.28)
. "$(dirname "$0")/lib.sh"
out=$(abspath "$1"); rm -rf "${out:?}"/*
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
# pgvector's Makefile defaults to -march=native, which ties the binary to the build machine's CPU; empty is its documented
# portable build (README: make OPTFLAGS=""). Overridable only so the baseline check below can be shown to fail.
EXT_OPTFLAGS=${EXT_OPTFLAGS-}
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
  make -C "$d" -j"$jobs" PG_CONFIG="$out/bin/pg_config" with_llvm=no OPTFLAGS="$EXT_OPTFLAGS" >"$work/$d.build.log" 2>&1 \
    || { tail -n 30 "$work/$d.build.log" >&2; exit 1; }
  make -s -C "$d" PG_CONFIG="$out/bin/pg_config" with_llvm=no OPTFLAGS="$EXT_OPTFLAGS" install >/dev/null
done
# The extensions must run on any CPU of the architecture. No compile line may target the build machine's CPU, and on
# x86-64 an AVX register may appear only in a function the code selects at run time after a CPU check: pgvector's
# target_clones variants (VectorL2SquaredDistance.fma, ...) and its AVX-512 and F16C paths, pg_textsearch's AVX2 path.
if grep -hE '(^| )-(march|mtune|mcpu)=native' "$work"/*.build.log >/dev/null; then
  echo "build-pg: an extension was compiled for the build machine's CPU (-march=native)" >&2; exit 1
fi
if [ "$os-$arch" = linux-x86_64 ]; then
  wide=$(for so in "$out"/lib/postgresql/vector.so "$out"/lib/postgresql/pg_textsearch*.so; do
    objdump -d --no-show-raw-insn "$so" | awk -v so="$(basename "$so")" '
      /^[0-9a-f]+ <.*>:$/ { fn = $2; next }
      /%[yz]mm/ && fn !~ /\.(fma|popcnt|avx[0-9a-z_]*)>:$|[Aa]vx|F16c|f16c/ { print so ":" fn }' | sort -u
  done)
  [ -z "$wide" ] || { echo "build-pg: AVX code outside a run-time selected function: $(echo $wide)" >&2; exit 1; }
fi

# What a server never needs: headers, static libraries, OpenSSL's command line and config, the client-only tools.
# OpenSSL's engines and loadable providers are unused (no TLS on a loopback server) and link libcrypto by absolute path.
rm -rf "$out/include" "$out/ssl" "$out/share/doc" "$out/share/man" "$out/lib/pkgconfig" "$out/lib/cmake" "$out/lib/postgresql/pgxs" "$out"/lib/*.a \
  "$out/lib/engines-3" "$out/lib/ossl-modules" "$out/bin/openssl" "$out/bin/c_rehash"
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
# CREATE EXTENSION runs no distance code: compute a distance and search an HNSW index, which an illegal instruction would stop.
dist=$("$moved/bin/psql" -h 127.0.0.1 -p "$port" -U postgres -d postgres -Atq \
  -c "SELECT round(('[1,2,3]'::vector <-> '[4,5,6]'::vector)::numeric, 4)" \
  -c "CREATE TABLE v (id int, e vector(3)); INSERT INTO v SELECT i, ARRAY[i, i % 7, i % 13]::real[]::vector FROM generate_series(1, 500) i;" \
  -c "CREATE INDEX ON v USING hnsw (e vector_l2_ops)" -c "SET enable_seqscan = off" \
  -c "SELECT string_agg(id::text, ',') FROM (SELECT id FROM v ORDER BY e <-> '[10,3,10]' LIMIT 3) s" 2>&1) || dist="$dist (failed)"
"$moved/bin/pg_ctl" -D "$t" -m fast -w stop >/dev/null
echo "pg: $("$moved/bin/postgres" --version); extensions: $ok; distance and hnsw: $(echo $dist)"
case $ok in *pg_textsearch=*vector=*OpenSSL*) ;; *) echo "build-pg: self-check failed" >&2; cat "$work/check.log" >&2; exit 1;; esac
case $(echo $dist) in "5.1962 10,"*) ;; *) echo "build-pg: vector self-check failed: $dist" >&2; cat "$work/check.log" >&2; exit 1;; esac
du -sh "$out"
