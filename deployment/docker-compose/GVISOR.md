# Install gVisor for coding engines (Docker Compose)

Coding engines (Codex, Claude Code, opencode) run the commands a model writes. On a production install PipesHub runs
each engine in a sandbox under **gVisor** (`runsc`), which gives the sandbox its own kernel. Without `runsc` on the
Docker host, engine runs are refused and admins see:

> Coding engines need the gVisor sandbox runtime on this server — install guide: (this page)

PipesHub's own code runs (`run_code`) and every other feature keep working without gVisor.

## Requirements

- A Linux host running Docker Engine (not Docker Desktop, WSL, rootless Docker or a remote daemon).
- `x86_64` or `aarch64`.
- Root or `sudo`.

## The installer does it

`install.sh` offers to install gVisor on a Linux Docker Engine. Installing edits `/etc/docker/daemon.json` and
reloads Docker, so `--yes` alone never does it:

```bash
./install.sh --install-gvisor        # or PIPESHUB_GVISOR=install ./install.sh
```

The installer downloads a pinned gVisor release, checks its SHA-512, installs `runsc` and its containerd shim,
registers the runtime with its own syscall filter (`--oci-seccomp`, see [Syscall filter](#syscall-filter)) and reloads
Docker. On a host whose `runsc` is already registered without the filter, the same flag (or answering yes when asked)
adds it and keeps the runtime's other arguments; `--yes` alone never changes the daemon.

## By hand

```bash
ARCH=$(uname -m)
URL=https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}
wget ${URL}/runsc ${URL}/runsc.sha512 ${URL}/containerd-shim-runsc-v1 ${URL}/containerd-shim-runsc-v1.sha512
sha512sum -c runsc.sha512 -c containerd-shim-runsc-v1.sha512
chmod a+rx runsc containerd-shim-runsc-v1
sudo mv runsc containerd-shim-runsc-v1 /usr/local/bin
sudo /usr/local/bin/runsc install -- --oci-seccomp   # adds "runsc" to /etc/docker/daemon.json
sudo systemctl reload docker                         # running containers keep running
```

Check that Docker lists it:

```bash
docker info --format '{{json .Runtimes}}' | grep -q '"runsc"' && echo "gVisor ready"
docker run --rm --runtime=runsc alpine uname -a   # prints a gVisor kernel
```

The sandbox broker reads Docker's runtimes on its own; PipesHub picks up `runsc` within about 30 seconds, with no
restart. The coding-engine settings then show the engines as available.

Register `runsc` with `--oci-seccomp` and nothing that weakens it. The broker does not offer gVisor from a daemon
whose `runsc` entry has `--allow-flag-override` (any container's annotations could then change gVisor's flags),
`--network=host`, `--net-raw`, `--host-uds`, `--host-fifo` or a `--TESTONLY-` flag; its log says `runscRefused=…`.
To stop PipesHub using gVisor without removing it from Docker, set `SANDBOX_WITHHOLD_RUNTIMES=runsc`.

## Syscall filter

gVisor answers every system call a sandbox makes from its own kernel, the Sentry, and only the Sentry talks to the
host. By default the sandbox's processes have no syscall filter of their own (`grep Seccomp /proc/self/status` reads
`0` inside): they may, for example, make user and network namespaces inside the Sentry. Those reach nothing outside,
but they are kernel surface a sandbox does not need. With `--oci-seccomp`, gVisor applies Docker's default seccomp
profile inside the Sentry too, as runc does on the host: a denied call gets the profile's `EPERM` and no namespace can
be made. That is a second layer, and the installer registers it by default.

Without it, coding engines still run on gVisor. The sandbox broker reports it (`gvisorSyscallFilter=off` in its
health and a `broker.gvisor_syscall_filter_off` warning in its log), and admins see on the coding-engine settings:

> Coding engines run on gVisor without its own syscall filter — one layer of defence short. how to turn it on

PipesHub warns rather than refuses: a host registered by `runsc install` with no arguments keeps the gVisor boundary,
and refusing would take every engine away on an upgrade with nothing gained in the meantime.

The other runsc flags stay at gVisor's defaults, which are already the strict ones: `--network=sandbox` (gVisor's own
network stack), `--host-uds=none`, `--host-fifo=none`, `--net-raw=false`, `--allow-flag-override=false`. `--directfs`
(on by default) is a performance trade-off gVisor ships as safe; it can be measured as a PF-09 variant before an
operator changes it.

### Turning it on for an existing install

Needs root on the Docker host. Changing `/etc/docker/daemon.json` and **reloading** Docker leaves every running
container running; a **restart** stops every container on the host (this daemon has `live-restore` off unless you set
it), so on a shared host warn its users before any restart and do it in a quiet window.

1. Tell the host's other users that the Docker daemon's configuration is changing (a reload; a restart only if the
   reload does not take).
2. Keep a copy: `sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak-$(date +%F)`.
3. Add the flag to the `runsc` entry, keeping anything else in the file (the `kata` entry, other settings):

   ```json
   {
     "runtimes": {
       "runsc": {
         "path": "/usr/bin/runsc",
         "runtimeArgs": ["--oci-seccomp"]
       }
     }
   }
   ```

   Use the `path` your file already has. `sudo runsc install --runtime=runsc -- --oci-seccomp` writes the same entry
   (it replaces the entry's arguments, so add any others the entry had after `--`).
4. Check the file parses: `sudo python3 -m json.tool /etc/docker/daemon.json >/dev/null && echo ok`.
5. Reload: `sudo systemctl reload docker` (a `SIGHUP`; Docker re-reads `runtimes` and new containers get the flag).
6. Verify:

   ```bash
   docker info --format '{{json .Runtimes.runsc}}'               # shows "runtimeArgs":["--oci-seccomp"]
   docker run --rm --runtime=runsc alpine grep Seccomp: /proc/self/status          # Seccomp: 2
   docker run --rm --runtime=runsc --cap-drop ALL alpine unshare -U true; echo $?   # Operation not permitted, 1
   ```

7. Within about 30 seconds the broker's health says `gvisorSyscallFilter=on` and the admin warning goes away.
8. If `docker info` still shows no `runtimeArgs`, the reload did not take: only then restart Docker
   (`sudo systemctl restart docker`), after the warning in step 1. Every container on the host stops and starts
   again with its restart policy.

To undo, restore the copy from step 2 and reload. Measure the flag's cost on your host with
`scripts/pf09_runtime_bench.py run … --runtime runc --runtime runsc --runtime-label runsc=runsc-oci-seccomp`.

## Dev and test installs without gVisor

Laptops and CI hosts often cannot run gVisor. For those only, set in `.env`:

```bash
PIPESHUB_ENGINES_WITHOUT_GVISOR=true
```

and restart PipesHub. Engines then run on hardened runc (read-only root, every capability dropped, no network but the
model and tool gateways), which shares the host's kernel. Admins see a warning that stays until the setting is
removed: "Coding engines run without gVisor — weaker isolation". Do not set it on a production install. The value
must be exactly `true`; anything else keeps the gVisor requirement. An organization whose engine profile asks for
gVisor or more (`isolationMin`) is refused either way.

This setting no longer decides whether the long-tail ACP engines (Gemini CLI, Goose, Qwen Code, DeepSeek Harness,
opencode over ACP) run. Their profiles are signed by the release build; a source checkout has them unsigned, and an
install reads unsigned profiles only with its own dev setting, `PIPESHUB_ACP_PROFILES_UNSIGNED=true`. Without it the
admin page shows each such engine as "This engine's profile isn't signed for this deployment". On a release image the
profiles are signed and nothing needs setting.

The setting and its default are defined once, in `contracts/harness/core/policy/release.json` (`engineIsolation`).

## Personal Claude accounts: the sign-in add-on

People can use their own Claude account in Claude Code (an admin allows it under **AI accounts and access**). They
sign in inside their own sandbox, so the sandbox must reach Anthropic's sign-in and API hosts over HTTPS. That needs
the egress proxy's tunnels, an add-on that is off by default. Without it the switches are greyed with:

> Personal Claude accounts need the sign-in add-on on this server — see the install guide

and anyone who turned their account on earlier has their Claude Code tasks run on the organization's model access,
with a one-line note in the chat.

To turn it on, start PipesHub with the tunnels overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.egress-tunnels.yml up -d
```

The overlay sets `EGRESS_CONNECT_TUNNELS=true` on the egress proxy and on the PipesHub service (its harness worker
reads it too), and gives the proxy, and only the proxy, a network off the host. The proxy cannot see inside a tunnel,
so a sandbox may reach only the hosts on its own allowlist, here the signed list Anthropic's sign-in needs. Within
about a minute of the worker starting, the switches become available; nothing else changes.
