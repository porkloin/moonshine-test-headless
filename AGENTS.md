# moonshine-test-headless: details

What README.md leaves out: every option, the refs it accepts, what each check means, the result files, the image's
podman flags, and how the repository is laid out. README.md is the short version for people.

## Layout

- `moonshine-test-headless`: the entrypoint, one bash script. `--help` prints its header.
- `battery.json`: the workloads, the measurements each is graded on, and the tiers (see Workloads).
- `targets.json`: each report card metric's target and acceptable value, its unit and which way is better, what it
  measures and the basis for its numbers. The default for `--targets`.
- `grafana/generate.py`: writes the test runs board (`grafana/test-runs.json`) and its report card's queries for
  `moonshine-otel summary` (`grafana/test-runs-queries.json`), from `battery.json` and `targets.json`, with
  moonshine-otel's panel helpers (`observability/grafana/generate.py` in `third_party/moonshine-otel`, or in the
  checkout at `MOONSHINE_OTEL`). Edit it rather than the JSON; a metric in targets.json without a query fails it.
- `Containerfile`: Arch Linux (its Mesa has the H.264 and H.265 video codecs enabled), the systemd user manager,
  PipeWire and Xwayland for Moonshine's session, the Rust toolchain, the workloads (vkcube, glxgears, hdrneon), and the
  test client built from source.
- `third_party/gamestream-test-client`: the headless Moonlight client (submodule).
- `third_party/moonshine-otel`: reads Moonshine's statistics socket, records every frame host to client as
  telemetry, compares recordings, and writes `summary.md` from a run's telemetry (submodule).
- `.github/workflows/image.yml`: builds the image, and pushes it to ghcr.io from `main` and weekly (to follow Arch's
  Mesa).

## Running

`podman container runlabel run IMAGE -- [options]` runs the `run` label; everything after `--` goes to the script
(without it, runlabel takes the options as its own):

```sh
podman container runlabel run ghcr.io/porkloin/moonshine-test-headless -- --pixelforge 123 [options]
podman container runlabel --display run ghcr.io/porkloin/moonshine-test-headless   # print the podman command
```

The labels bind-mount the current directory at `/results`; each run writes `moonshine-test-<time>-<refs>/` there. Labels:

| Label | |
|---|---|
| `run` | AMD and Intel (`--device /dev/dri`, `--group-add keep-groups` so the render group carries over) |
| `run-nvidia` | NVIDIA through the container toolkit's CDI device `nvidia.com/gpu=all` |
| `run-remote` | like `run`, publishing the ports a client on another machine needs; pass `--client USER@HOST` (below) |
| `serve` | build, then serve the Moonshine build on the GameStream ports for a real Moonlight client; type the PIN Moonlight shows into the terminal; each workload is an app, titled with its id |

Every label passes `--systemd=always` and `--security-opt label=disable` (Moonshine's session is a systemd user
manager that manages its cgroup, which SELinux would otherwise need a host boolean for), `--cap-add SYS_PTRACE`
(Moonshine calls `kcmp`, which podman's seccomp profile only allows with it, inside the user namespace),
`--pids-limit -1` (parallel builds and links exceed podman's default of 2048), `--shm-size 1g`, and `--uts=host` with
read-only mounts of the host's `/sys` and `/etc/os-release` (the report names the host, its model, link and OS). The image sets
`ANV_DEBUG=video-encode`, without which Intel's Vulkan driver offers no video encode.

The container is rootless with its own network namespace. Everything inside runs as the container's root, which is
the invoking user outside, so GPU access matches theirs. It sees the GPU, the current directory and the
`moonshine-test-headless-cache` volume (cargo registry, git mirrors, build targets; about 10 GB).

## Refs

`--moonshine`, `--pixelforge` and `--ferriscope` each take a ref of that project (at most one each; at least one
is needed). For `--pixelforge`:

```text
123 or #123                    pull request 123 on hgaiser/pixelforge
owner@branch-or-sha            a branch, tag or commit on owner/pixelforge
owner#123                      pull request 123 on owner/pixelforge
owner/repo#123, owner/repo@ref the same on a repository with any name
owner/repo                     its default branch
https://github.com/...         a pull request, tree or commit link
./pixelforge                   a checkout in the current directory
```

`--baseline-moonshine`, `--baseline-pixelforge` and `--baseline-ferriscope` take the same forms.

A path is taken as it is, uncommitted changes included: the tracked and untracked files git doesn't ignore are copied
in, and the report shows its commit with `-dirty` when it has changes. A `git worktree` checkout doesn't work: its `.git` points at a repository outside the
mount.

Moonshine is always built: from its ref, or hgaiser/moonshine's default branch. pixelforge and ferriscope refs
replace the versions Moonshine depends on, through `cargo --config 'patch."<source>".<crate>.path=...'`, with the
source URL read from Moonshine's `cargo metadata`. A ref whose version doesn't fit Moonshine's dependency fails the
build with "was not used in the crate graph".

## Options

| Option | |
|---|---|
| `--seconds N` | length of each stream, and of each `--baseline` stream after 2 s of warmup (20) |
| `--codecs LIST` | comma-separated subset of `h264,hevc,av1` (default: all Moonshine offers on the GPU) |
| `--no-stream` | only the GPU tests |
| `--baseline` | also build hgaiser's default branches, time the same streams from both builds, compare |
| `--baseline-PROJECT REF` | baseline for that project (`moonshine`, `pixelforge`, `ferriscope`) instead of hgaiser's default branch (implies `--baseline`) |
| `--iterations N` | timed streams per codec and build with `--baseline` (5); loss and click streams run once |
| `--chaos OPTIONS...` | gamestream-test-client options for each codec's second stream instead of `--loss-frame-every 120`: everything after `--chaos`, so it goes last, e.g. `--chaos --loss-bursty 2:8 --no-rfi`. The row is named after them |
| `--no-traces` | don't record telemetry (on by default, `--traces` still accepted), so no summary beyond the checks. With it: moonshine-otel's OTLP metrics (exported every second) and traces from the streams in `logs/otlp/`; every frame is a trace with Moonshine's and the client's spans (each stream gets a key for `watch --stream-key-file` and `moonshine-otel client`, which follows the client's `--stats` live, or reads them once fetched back from a remote client, labeling its `moonshine.client.*` metrics with the workload, codec, stream kind, `run` (the code under test as `<asked for>@<commit>`, then the run's start in UTC: `#257@5631ba0 · 11-03 22:13Z`, or `local@5631ba0-dirty · …` for a checkout), `side` (test, or base for the baseline streams), that side's `code` (`#257@5631ba0`), `ref` and `commit`, and the workload's `load`; at the run's first stream, `moonshine-otel info` records the host and the client (the same fields: machine, CPU, memory, the GPU and driver it encoded or decoded on, other GPUs, OS, kernel, network; N/A where the probe had none) and the run (code, baseline, the route and round trip, never an address, and the stream settings) (`logs/info.log`); `logs/client-traces.log`). Workloads with `"log": true` in battery.json (clickflash, hdrneon) also get `--log /work/app-events.jsonl`, emptied before each stream, and `moonshine-otel app` follows it here with the same key and labels (`logs/app-traces.log`). The client times frames on its own machine, so it also gets `--clock`: `gamestream-test-client clock-serve` runs here on base port + 22 (48011, or 58011 remote) and answers NTP with this machine's clock, the one Moonshine stamps frames with; the client measures it before and after each stream and gives its times on it, with the offset, its uncertainty (half the fastest NTP round trip) and the drift during the stream recorded on its spans and as metrics. No machine's clock is set or needs syncing; `logs/clock-serve.log` |
| `--tier NAME` | the battery's tier: `quick`, `standard` (default when no `--app`) or `full` |
| `--app NAME` | a workload instead of a tier (repeatable): an id from the battery, `gravitymark:N` for N asteroids (1m by default) |
| `--battery FILE` | the battery (workloads, measurements, tiers) from FILE instead of the image's `/usr/share/moonshine-test-headless/battery.json` |
| `--targets FILE` | score the summary's report card against FILE's targets (`targets.json`'s format; it's the default), passed to `moonshine-otel summary --targets` |
| `--app-command CMD` | also stream `bash -c CMD` as workload `custom`, measured like vkcube and compared with `--baseline`; the current directory is `/results`, for your own binaries. One word: runlabel splits arguments on spaces, so for a command with arguments put it in a script in the current directory (`--app-command /results/run.sh`) |
| `--hdr` | every workload streams HDR (as `hdr: true` in the battery does for one): `[compositor] hdr = true` in Moonshine's config, the client asks for HDR, only codecs with a 10-bit profile (HEVC Main10, AV1 Main10; H.264 is skipped), and workloads with a `command_hdr` run that |
| `--mode MODE` | the stream's mode, repeatable: a name from the battery's `modes` or `WIDTHxHEIGHT@FPS[:KBPS]`. Every client stream gets `--mode WxHxFPS --bitrate KBPS`. Without a bitrate: `--bitrate`, else Moonlight's default (`moonlight_kbps`, moonlight-qt `getDefaultBitrate`: a factor by pixel count, 1 at 360p to 40 at 4K, interpolated and held at 40 above, times fps/30, sqrt-scaled above 60 fps). Each mode streams the whole run again into its own results (below); builds and GPU tests happen once and their checks are in every mode's report. Default `1080p60` |
| `--bitrate KBPS` | bitrate for modes without one |
| `--client USER@HOST` | stream to the client on another machine: one SSH connection for the run (`ControlMaster`, `/tmp/mth-ssh-%C`; the first asks for the password on the terminal, later ones run with `BatchMode`), closed at exit. Every remote command goes to `sh -s` on stdin, so the remote's login shell (fish, zsh) doesn't matter. Each client command runs `podman run --rm --network host --device /dev/dri --group-add keep-groups -v mth-client-keys:/keys -v ~/.cache/mth-client/out:/out` of `--client-image` there, with `--host` the address the remote sees this machine at (`$SSH_CONNECTION`, so a tailnet address over Tailscale); `--json`/`--csv`/`--dump` files are written to `/out` and copied back. The client machine is described by `gamestream-test-client probe` in the client image (`--network host --uts=host --device /dev/dri`, the remote's os-release mounted); RTT from 10 pings. The probe's facts go into the run's `moonshine.test.info` (role=client), the route and round trip into role=run. Needs the `run-remote` label. Ports: Moonshine's config moves them to Sunshine's layout around HTTP 57989 (`[webserver]` HTTPS 57984, `[stream]` RTSP 58010, video 57998, control 57999, audio 58000; the label publishes those, and 58011 UDP for `clock-serve`), so a Moonshine on the host keeps the defaults; the client gets `--port 57989` and learns the rest from `/serverinfo` and RTSP. Loopback runs keep the defaults |
| `--client-image IMAGE` | the client image the remote runs: by default `/usr/share/moonshine-test-headless/client-image`, which the image build writes from `--build-arg CLIENT_IMAGE` (CI: `gamestream-test-client:sha-<submodule commit>`, so the remote runs the same client as loopback), else `:latest`. Pulled on the remote each run, or used as is when it's already there (`podman load`). Preflight: podman on the remote, a render node it can open (else software decode), and once Moonshine is up, a TCP connect from the remote to `host:47989` (else the "Remote client" check fails, naming the ports) |
| `--decode HOW` | the client's decoding: `hw` (default), `sw`, or a render node. The harness picks the device once from the client's probe and passes `--decode-device`: `hw` takes Moonshine's own GPU over loopback (encode and decode on one device; else the VAAPI GPU with the most video memory), and on a remote client its VAAPI GPU with the most video memory; with no VAAPI GPU (NVIDIA), software. Codecs the device doesn't decode (`decodes` in the probe; HDR HEVC needs `hevc10`) are skipped with a row, like codecs Moonshine doesn't offer, so software decoding never lands in the numbers. The summary's client GPU and driver are that device's. Each stream's client summary records its `decoder`. Bench streams keep `--decode none`. NVIDIA (`run-nvidia`, no `/dev/dri`, no NVDEC VAAPI driver in the image) decodes in software |
| `--accept-gravitymark-license` | accept GravityMark's license without being asked |
| `--strict` | exit 1 when a check failed, for CI |
| `--log FILTER` | `MOONSHINE_LOG` for Moonshine and `RUST_LOG` for the GPU tests: a level or per-crate filter, e.g. `pixelforge=debug,ferriscope=trace` (info); at debug or trace the client logs the protocol too |
| `--serve` | what the `serve` label passes |

## Workloads

`battery.json` (in the image at `/usr/share/moonshine-test-headless/battery.json`) lists named modes (`"4k120":
"3840x2160@120"`), the workloads and the tiers.
Moonshine's config gets an app per workload of the run, titled with its id. A workload entry:

| Field | |
|---|---|
| `title`, `description` | for the report |
| `command`, `command_hdr` | argv run in the stream's session; `command_hdr` instead when it streams HDR; `["@gravitymark"]` is GravityMark (below) |
| `load` | `low` or `high`, a label on its streams' metrics |
| `warmup_s` | seconds from each stream's first frame that the client's metrics leave out (`moonshine-otel client --warmup`), for a workload that takes a while to start rendering: GravityMark loads for about 4 s at about a frame a second. Its spans are still sent, marked `warmup`; the check row counts the whole stream and says so. Default 0 |
| `hdr` | always stream it in HDR |
| `streams` | which streams per codec: `clean` (`--seconds`), `loss` (with `--loss-frame-every 120`, or `--chaos`), `click` (at least 60 s, clicking every 300 ms) |
| `measures` | the measurements that apply to it, which streams it gets and which panels the test runs board gives its section (`grafana/generate.py`): `correctness`, `smoothness`, `server_latency`, `loss_recovery`, `click_latency`, `hdr_transitions`, `load`, `bitrate` |
| `switch_every_s` | seconds between content switches, for `hdr_transitions` |
| `baseline` | stream it for the `--baseline` comparison: timed streams when it measures `server_latency` or `smoothness`; loss streams with the same `--chaos` (or frame drop) when it measures `loss_recovery`; a click-to-flash stream when it clicks |

| Id | Command | Streams | Where it comes from |
|---|---|---|---|
| `vkcube` | `vkcube --wsi wayland` (its 500×500 window, scaled up by ferriscope) | clean, loss | Arch's vulkan-tools |
| `glxgears` | `glxgears -fullscreen` (under Xwayland) | clean, loss | Arch's mesa-utils |
| `glxgears:no-vsync` | `vblank_mode=0 glxgears -fullscreen` (under Xwayland) | clean, loss | the same, presenting with swap interval 0 instead of vsync |
| `hdrneon` | `hdrneon --fullscreen --mode sdr` (`hdr10` with `--hdr`) | clean, loss | its latest GitHub release, checked against `SHA256SUMS` at image build |
| `hdrneon-cycle` | `hdrneon --fullscreen --mode hdr10 --cycle 1`, always HDR | clean, loss | the same |
| `clickflash` | `clickflash --fullscreen` | click | the same |
| `clickflash:no-vsync` | `clickflash --fullscreen --no-vsync --flash-ms 34` | click | the same, presenting with mailbox or immediate instead of FIFO; each flash stays up 34 ms so the compositor catches it |
| `gravitymark[:N]` | `GravityMark.x64 -vulkan -fullscreen 1 -width $MOONSHINE_CLIENT_WIDTH -height $MOONSHINE_CLIENT_HEIGHT -vsync 0 -asteroids N -nosensors 1 -fps 0 -info 0` (Moonshine sets those to the stream's size) | clean, loss | downloaded at run time, see below |

Tiers: `quick` = vkcube; `standard` = vkcube, hdrneon-cycle, clickflash; `full` = standard + gravitymark.

GravityMark is Tellusim's GPU stress test, under an evaluation license (testing only, no commercial use) that forbids
redistributing it, so it isn't in the image. On first use the script downloads
`https://tellusim.com/download/GravityMark_1.89.run` into `/cache/apps/` (SHA-256 pinned in the script), shows the
license text embedded in that installer, and asks for `y`. Only then does it run the installer
(`--noexec --nox11 --target /cache/apps/gravitymark-1.89`, answering its own prompt with that `y`). Later runs reuse
the install. `--accept-gravitymark-license` is the person running it accepting without the question. Without a
terminal, or with runlabel's terminal and nobody typing, the question times out after 10 minutes and the run stops,
so scripts and agents should pass the flag (when the person they run for has accepted the license) or pick another
workload.

## Checks

In order; a failed build stops the run.

| Check | Passes when |
|---|---|
| Fetch | the ref resolves (PR heads through `pull/N/head`; short hashes after fetching all branches and tags) |
| pixelforge GPU tests | `cargo test --no-fail-fast -- --ignored --test-threads=1` passes, under the Vulkan validation layer (`VK_LOADER_LAYERS_ENABLE=*validation`); notes list failed test names |
| pixelforge validation, ferriscope validation | never fails: `ok` with no validation layer messages, else `messages` listing each VUID, how often, and the test binaries it came from (the layer can lag a driver's extensions) |
| ferriscope GPU tests | `cargo test -p ferriscope --no-fail-fast -- --ignored --test-threads=1` passes, under the validation layer (needs Xwayland, xev, xdotool, xprop, which the image has) |
| Build Moonshine | ferriscope's WSI layer (`dist/install-wsi.sh`) and `cargo build --release --bin moonshine` succeed |
| Host timing | skipped when the Moonshine under test has no statistics socket (`src/stats_socket.rs`) |
| `<codec> streams` | skipped when Moonshine doesn't advertise the codec (`ServerCodecModeSupport` in `/serverinfo`) |
| HDR | with an HDR workload and no statistics socket: skipped, since nothing confirms the streams were HDR |
| `<workload>: <codec> HDR` failing with "asked for HDR, Moonshine streamed SDR" | an HDR workload with the statistics socket: the stream moonshine-otel recorded wasn't HDR |
| `<workload>: <codec>` failing with "asked for WxH@FPS at K kb/s, Moonshine streamed ..." | with the statistics socket: the stream moonshine-otel recorded (`runs[0].config` width, height, target_fps, bitrate) isn't the mode asked for |
| `<workload>: <codec>`, and `<workload>: <codec>, packet loss` | the client streamed (exit 0), got frames, saw no decode errors or corrupt frames, and stalls covered less than half the stream; "packet loss" drops every packet of one frame every 120 frames (`--loss-frame-every 120`); its notes start with how many frames were dropped on purpose |
| `<workload>: <codec>, loss 1` (, baseline) | with `--baseline`, per workload measuring loss recovery: one stream per codec from each build with the loss stream's options and `--seed 1` on both, so both see the same losses; a row only when one fails |
| `<workload>: <codec>, clicks` (, baseline) | a click stream: at least 60 s (`--seconds` if longer) clicking every 300 ms (`--click-every 300`); ok when at least nine clicks in ten flashed and every picture decoded. With `--baseline`, each build streams one more |
| Speed vs baseline | with `--baseline`: skipped when either Moonshine has no statistics socket (click-to-flash streams still run) |
| Benchmark (baseline) Moonshine, `<workload>`, `<codec>` / `<workload>: speed <codec> vs baseline` | with `--baseline`, per comparing workload that's timed: N streams of `--seconds` + 2 s per codec from each build (`--decode none`, nothing dropped), recorded by `watch`, then `moonshine-otel compare`: exit 0 ok, 1 FAIL (slower), 2 skipped (no verdict) |

The exit code is 0 when the run finished, whatever its checks say, and 2 for usage errors or no GPU. With `--strict`, a failed check makes it 1. Through `runlabel`, a
non-zero exit shows as 125.

## How a run goes

1. `vulkaninfo --summary`; the first non-llvmpipe device names the GPU in the report.
2. Fetch every ref into `/work/<side>/<project>` through bare mirrors in `/cache/git` (side `test`, and `base` with
   `--baseline`).
3. Start a journal and a systemd user manager (Moonshine launches apps as transient units on it).
4. GPU tests for the pixelforge and ferriscope refs.
5. Build Moonshine with the patched dependencies.
6. Start it with a config that has an app per workload, plus `[stats] socket = "/run/user/0/moonshine/stats.sock"` when it has the
   socket; then `moonshine-otel watch` (moonshine-otel's own build, unpatched: it only reads the socket)
   records each stream into `/work/server/`.
7. Pair the client automatically: it sends a pairing request with PIN 0000, and the script posts the PIN with the
   client's unique id to Moonshine's localhost-only `/submit-pin`.
8. With `--baseline`: build the baseline Moonshine.
9. Per mode (`mout`, `mlogs`, `mwork`: the run's folder, its `logs/` and `/work` with one mode; a folder per mode and
   `/work/<mode>` with more): start the Moonshine under test, then per workload and codec the streams the workload
   asks for, each with `--quit` so the next starts a new session; clean streams sample GPU busy
   (`/sys/class/drm/card*/device/gpu_busy_percent`, the busiest card, or `nvidia-smi`) every 200 ms into
   `<stream>.gpu.csv`. With `--baseline`, start each build in turn and run the comparing workloads' streams (timed
   ones recorded by `watch` into `<mwork>/bench/<side>/<workload>/<codec>/`), then compare the two sides' recordings
   per workload and codec, each comparison a check. Then copy the spool to `logs/otlp/`, and run `moonshine-otel summary logs/otlp --run
   <the mode's run> --notes <the checks as Markdown>` with the image's Prometheus (`/usr/local/bin/prometheus`,
   from prom/prometheus) for `summary.md`. Each mode is a run of its own (` · <mode>` on the run name with more
   than one). Without telemetry, moonshine-otel or a recorded stream, `summary.md` is the checks only and says why.
10. With more than one mode, the top `summary.md` is each mode's in turn; then the tarball, whatever happened, and
    the paths of `summary.md` and `results.tar.gz` with how to add them to a pull request.

## Results

`moonshine-test-<UTC time>-<project>-<ref>.../` in the current directory, or with more than one `--mode`, the combined `summary.md`, the shared logs
(builds, fetches, Moonshine, `otlp/`) and `<W>x<H>@<FPS>-<KBPS>k/` per mode holding the per-mode files
(`summary.md`, the stream logs under its own `logs/`):

| Path | |
|---|---|
| `summary.md` | for the pull request: hardware (host and client), the stream, the report card (the test runs board's tiles, from `test-runs-queries.json`), then the checks |
| `logs/summary.log` | `moonshine-otel summary` output |
| `logs/run.log` | the terminal output |
| `logs/vulkaninfo.txt` | `vulkaninfo --summary` |
| `logs/fetch-<side>-<project>.log` | fetch output |
| `logs/<project>-gpu-tests.log` | cargo test output |
| `logs/[baseline-]wsi-build.log`, `logs/[baseline-]moonshine-build.log`, `logs/moonshine-otel-build.log` | build output |
| `logs/[baseline-]moonshine.log`, `logs/[baseline-]moonshine-otel.log`, `logs/user-manager.log`, `logs/pair.log` | runtime logs |
| `logs/<workload>-<codec>-<clean\|loss\|click>.log`, `.json`, `.csv` | the client's summary, its JSON form, and one row per frame |
| `logs/<workload>-<codec>-clean.gpu.csv` | GPU busy percent every 200 ms (`ms,busy`), when the GPU reports it |
| `logs/timing/<workload>-<codec>-<stream>.json`, `.frames.csv` | moonshine-otel's result for that stream (schema in moonshine-otel) and its per-frame stage timings |
| `logs/bench/<test\|base>/<workload>/<codec>/` | with `--baseline`: per timed stream, moonshine-otel's result and `.frames.csv`, the client's log and `stream-<n>.csv`; `loss-<n>.json`, `.csv`, `.log` for loss streams; `click.json` for click streams |
| `logs/bench/compare-<workload>-<codec>.md` | the comparison |
| `logs/otlp/` | OTLP batches, the run's telemetry: what `summary.md` is made from (`moonshine-otel summary results.tar.gz` makes it again), what the `:view` image loads (see Exploring a run) |
| `results.tar.gz` | everything above, the bundle to attach to the pull request |

The client's JSON fields are `frames`, `seconds`, `fps`, `idr`, `missing`, `decode_errors`, `corrupt`, `stalls`,
`stalled_ms`, `interval_ms_p50/p95/p99/max`, `video_packets`, `dropped_packets`, `dropped_frames`, `seed`,
`recovery_frames`, `ack_requested_frames`, `idr_requests`, `rfi_requests`.

## The test runs board

`grafana/test-runs.json` (`test_runs()` in `grafana/generate.py`) shows a run beside another, independent of the time range as long as it covers both (default the last 7 days). A and B
are each a run, picked by `run` (`local@5631ba0-dirty · 11-03 22:42Z`), and a side of it (`side`: `test`, or `base`
for a `--baseline` run's baseline build, whose streams share the run). Side A lists `test` first and side B `base`,
so a run with a baseline opens compared with it, and B vanishes when it's the same as A. Runs are named everywhere else by their code, `<asked
for>@<commit>` (the `code` label), with tiles' A/B by commit. Each run gets the same two rows, run B's folded: a report card
with a tile per metric of targets.json, colored by its thresholds (a tile's ⓘ says what it shows, the metric's
`what`, and where the thresholds are; the reasoning behind them stays in targets.json), then *Machines + stream
config*, a row of its own so it can be folded: the host and client (the same fields, N/A where the probe had none) and the code and
stream from `moonshine.test.info`, with the run's streams, frames decoded and clock uncertainty merged in.
Then a folded section per battery workload with the measurements battery.json binds to it: their tiles, A beside B,
and their charts (histograms from the native histograms with `histogram_fraction`: frame pacing in tenths of a frame on a log scale, recovery in whole frames, click to photon per millisecond up to two frames, one chart per codec; p50–p99.9 bars for host and app latency; Moonshine's mean time per stage stacked in pipeline order, a bar per run; and timelines on seconds since the
workload's first frame in run A: each query a subquery pinned to that workload's window, `[len_<w>:1s] @ a_end_<w>`
from hidden variables per workload, with B's selectors offset by the difference of its starts, in a Trend panel).
Every B query is guarded by `DIFFERENT_RUNS`, empty when run B is run A, so picking one run twice shows one number,
one set of bars and one line instead of the same run twice. Moonshine's series are joined to the client's by `stream_key` in each query, so
every panel can filter by workload. Then the clocks, and Tempo tables of freezes and drops, the slowest frames and
the slowest clicks.

To add the board to moonshine-otel's stack:

```sh
dir="$HOME/.local/share/moonshine-otel/observability/grafana/dashboards/Moonshine tests"
mkdir -p "$dir" && cp grafana/test-runs.json "$dir/"
```

`grafana/test-runs-queries.json` is the report card's tiles as PromQL, `{sel}` for the run, in targets.json's
order, for `moonshine-otel summary --queries` (the image has it, and targets.json, in
`/usr/share/moonshine-test-headless/`). Regenerate both after changing the board, targets.json or battery.json:

```sh
python3 grafana/generate.py                                  # with third_party/moonshine-otel checked out
MOONSHINE_OTEL=../moonshine-otel python3 grafana/generate.py # or another checkout
```

## Exploring a run

`ghcr.io/porkloin/moonshine-test-headless:view` (`view/Containerfile`, on Grafana's image, built and pushed with
`:latest`) opens bundles in a throwaway Grafana:

```sh
podman container runlabel run ghcr.io/porkloin/moonshine-test-headless:view PATH...
```

Each PATH is a `results.tar.gz`, a run's folder, or its `logs/otlp`, under the current directory (mounted read-only
at `/results`). With two or more, the link sets run A to the first bundle's run and run B to the second's, both
`test`; any loaded run can be picked as A or B. `view/moonshine-test-view` starts Prometheus, Tempo and Grafana in the container, posts every batch
of the spool to them (metrics in file-name order, which is time order; traces 8 at a time), and prints a link to
the test runs board over the runs' time range. The container shares the host's network (`--network host`) and every
service listens on 127.0.0.1 only, each on a port picked as it starts (Grafana from 3333 up, from
`MOONSHINE_TEST_VIEW_PORT` when set; Prometheus, Tempo's HTTP, gRPC and internal servers, memberlist and the OTLP
receiver from 19090 up; a port that a concurrent viewer took in between moves the service to the next free one), so
several viewers, or a stale one, run side by side and nothing is reachable from outside; the printed link carries
Grafana's port. Grafana is anonymous admin, with the test runs
board and moonshine-otel's boards. Everything lives in a temp directory and goes with the container.

The image's `summary` label makes `summary.md` again from a bundle, on stdout:
`podman container runlabel summary ghcr.io/porkloin/moonshine-test-headless:view PATH [OTHER]`.
`view/moonshine-test-summary` runs `moonshine-otel summary` (built static on musl in the image) with the image's
copies of `test-runs-queries.json` and `targets.json`; with OTHER, as `--baseline OTHER`, the two side by side. The
harness's checks (GPU tests, validation) aren't in the bundle's telemetry, so the remade report has no checks section.

The `compact` label writes `NAME-compact.tar.gz` next to a bundle (the current directory mounted writable):
`view/moonshine-test-compact` runs `moonshine-otel compact` on its `logs/otlp`, which drops every metric point that
repeats its series' previous value. The exporter sends cumulative values every 5 s and keeps sending every series it
has seen, so most points in a long run repeat ended streams. A full `--baseline` run went from 45 MB to about 21 MB,
with the same `summary.md`. Traces are kept as they are. The harness keeps the full bundle, and prints the command
when it's over 25 MB.

Old data needs settings the long-running stacks don't have: Prometheus takes samples out of order within 365 days
and keeps them for 100 years (retention counts from the newest sample); Tempo dates blocks by their spans
(`ingestion_time_range_slack`), keeps them (`block_retention`), searches the ingester for any range
(`query_ingesters_until`), and has its ingestion rate limits raised, since the default turns away about a third of
a run's trace batches.

## Building the image

```sh
git clone --recursive https://github.com/porkloin/moonshine-test-headless
cd moonshine-test-headless
podman build -t moonshine-test-headless .
podman container runlabel run localhost/moonshine-test-headless -- --moonshine hgaiser/moonshine
```

Check the script with `bash -n moonshine-test-headless`. It runs under `set -euo pipefail`: a pipeline whose first
command finds nothing (`grep`, `ls`) ends the script, so count with globs or awk.
