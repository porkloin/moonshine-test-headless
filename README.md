# moonshine-test-headless

Test [Moonshine](https://github.com/hgaiser/moonshine), [pixelforge](https://github.com/hgaiser/pixelforge) and/or
[ferriscope](https://github.com/hgaiser/ferriscope) pull requests on your own hardware with one command. Includes
client telemetry and timing from a simulated Moonlight client (loopback) or one on another machine on your LAN
(SSH), and per-frame trace data. Compare it all in Grafana or in Markdown reports.

![The test runs board in Grafana, showing a run's report card and its vkcube section](docs/board.png)

## Prerequisites

- Linux with an AMD, Intel or NVIDIA GPU
- [podman](https://podman.io/docs/installation)
- NVIDIA only: the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
  (already on Bazzite and other Universal Blue NVIDIA images; `nvidia-ctk cdi list` shows `nvidia.com/gpu=all` when
  it's set up)

> [!NOTE]
> Host latency and `--baseline` need [hgaiser/moonshine#257](https://github.com/hgaiser/moonshine/pull/257), which
> isn't merged yet.

## Quickstart

### Run a test

```sh
podman container runlabel run ghcr.io/porkloin/moonshine-test-headless \
  -- --pixelforge 123
```

On NVIDIA, use `run-nvidia` instead of `run`. Results go to a new `moonshine-test-...` folder.

### View your run

```sh
podman container runlabel run ghcr.io/porkloin/moonshine-test-headless:view \
  moonshine-test-.../results.tar.gz
```

Open the link it prints. Ctrl+C stops it.

### Share it

Paste `summary.md` into the pull request, and attach `results.tar.gz` to the same comment. Anyone with the file can
view it the same way.

GitHub only takes attachments up to 25 MB. For a bigger bundle, make a smaller copy with the same results,
`results-compact.tar.gz`, and attach that instead:

```sh
podman container runlabel compact ghcr.io/porkloin/moonshine-test-headless:view \
  moonshine-test-.../results.tar.gz
```

If that's still too big (a full `--baseline` run usually is), add `--no-traces`. Traces are most of the telemetry;
without them you get `results-compact-no-traces.tar.gz`, with the same summary but no per-frame traces in Grafana.

### Compare two runs

```sh
podman container runlabel run ghcr.io/porkloin/moonshine-test-headless:view \
  mine.tar.gz theirs.tar.gz
```

The board opens with the first as run A and the second as run B.

### Make a Markdown report

```sh
podman container runlabel summary ghcr.io/porkloin/moonshine-test-headless:view \
  results.tar.gz > summary.md
```

Give it a second bundle to put the two runs side by side.

## Other clients

By default the client is simulated on the same machine. To stream across a real network instead, run the test client
on another machine over SSH. That machine needs podman, and SSH asks for its password once:

```sh
podman container runlabel run-remote ghcr.io/porkloin/moonshine-test-headless \
  -- --pixelforge 123 --client you@other-machine
```

To play the build yourself in Moonlight, serve it. Pair by typing the PIN Moonlight shows into the terminal; each
workload shows up as an app:

```sh
podman container runlabel serve ghcr.io/porkloin/moonshine-test-headless \
  -- --pixelforge 123
```

`serve` uses Moonshine's default ports, so stop any Moonshine already running on the machine first.

## Workloads

What gets streamed. `--tier` picks a set; `--app NAME` picks workloads one by one.

| Workload        | Tiers                  | What it tests                                                                 |
| --------------- | ---------------------- | ----------------------------------------------------------------------------- |
| `vkcube`        | quick, standard, full  | A spinning cube with almost no GPU load, so what's measured is Moonshine      |
| `hdrneon-cycle` | standard, full         | Content switching between SDR, HDR10 and scRGB every second, as games do      |
| `clickflash`    | standard, full         | Click-to-photon latency: the screen flashes on each click                     |
| `gravitymark`   | full                   | A GPU stress test that saturates the GPU Moonshine encodes on, like a heavy game |
| `glxgears`      | `--app` only           | Low load through Xwayland, for an X11 app                                     |
| `hdrneon`       | `--app` only           | Bright, saturated motion that keeps the encoder busy, in SDR or HDR10 (`--hdr`) |

GravityMark is downloaded on first use and asks you to accept its license in the terminal, so run `--tier full`
interactively (or pass `--accept-gravitymark-license`).

## Timing

|                | Alone   | With `--baseline` |
| -------------- | ------- | ----------------- |
| `--tier quick` | ~2 min  | ~3 min            |
| Default        | ~15 min | ~35 min           |

Tested on a Ryzen 9 5950X with a Radeon RX 7900 XTX.

## Options

Put them after `--`.

- `--moonshine`, `--pixelforge`, `--ferriscope`: what to test of each, at least one:
  - `123`: a pull request on hgaiser's repository
  - `someone@my-branch`: a branch, tag or commit of someone's fork
  - a GitHub link
  - `./pixelforge`: your checkout in the current folder, uncommitted changes included
- `--baseline`: compare against hgaiser's default branches
- `--tier quick` or `--tier full`: fewer or more workloads
- `--iterations N`: timed streams per workload with `--baseline` (default 5)
- `--mode 4k120`: stream resolution and frame rate (default `1080p60`)
- `--help`: everything else, also documented in [AGENTS.md](AGENTS.md)

## License

GPL-3.0
