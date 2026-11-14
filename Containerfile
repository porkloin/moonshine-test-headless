# moonshine-test-headless: builds pixelforge, ferriscope and Moonshine at the refs it's given, runs their GPU
# tests, and streams from the Moonshine it built to a headless Moonlight client.
#
#   podman build -t moonshine-test-headless .
#
# Arch, because its Mesa has the H.264 and H.265 video codecs enabled.
#
# The tools it uses, pinned here (bump them here, only to what their main branch has): gamestream-test-client at a
# commit on its main branch, built against this image's packages, and its image of that commit (sha-COMMIT, which
# its CI publishes for every main commit) for --client runs on another machine; and a moonshine-otel release, built
# at run time (view/Containerfile pins the same release); and a framestamp release, whose picture the client's
# --check-picture draws again, so the two move together.
ARG GAMESTREAM_TEST_CLIENT_COMMIT=1baef19
ARG MOONSHINE_OTEL_VERSION=v1.4.0
ARG FRAMESTAMP_VERSION=v0.1.0

FROM docker.io/library/archlinux:latest
LABEL org.opencontainers.image.source=https://github.com/porkloin/moonshine-test-headless
# The run labels also mount the host's /sys and os-release read-only and share its hostname (--uts=host), so the
# report can name the host, its model, network link and OS (the container only sees its own).
# Moonshine's session: the systemd user manager starts each app as a transient unit, PipeWire carries the audio,
# Xwayland serves X11 apps. Then the GPU drivers (with VAAPI for the client's hardware decode: Mesa's for AMD,
# intel-media-driver for Intel), the Vulkan validation layer for the GPU tests, the toolchain for the code under
# test, and the test tools.
RUN pacman -Syu --noconfirm --needed \
      systemd dbus-broker-units pipewire pipewire-pulse wireplumber xorg-xwayland \
      mesa mesa-utils vulkan-radeon vulkan-intel vulkan-icd-loader vulkan-tools vulkan-validation-layers xorg-xev xorg-xprop xdotool xcursor-themes procps-ng \
      base-devel rust clang cmake libc++ pkgconf git jq catatonit \
      opus libevdev libxkbcommon wayland \
      openssl curl expat util-linux-libs ffmpeg \
      libva-utils intel-media-driver openssh libnl hwdata \
    && pacman -Scc --noconfirm
# gamestream-test-client, built here so it links against these packages.
ARG GAMESTREAM_TEST_CLIENT_COMMIT
RUN git clone --quiet https://github.com/porkloin/gamestream-test-client /tmp/gtc \
    && git -C /tmp/gtc -c advice.detachedHead=false checkout --quiet "$GAMESTREAM_TEST_CLIENT_COMMIT" \
    && git -C /tmp/gtc submodule update --quiet --init --recursive --depth 1 \
    && cmake -S /tmp/gtc/client -B /tmp/gtc-build -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DGTC_COMMIT="$GAMESTREAM_TEST_CLIENT_COMMIT" > /dev/null \
    && cmake --build /tmp/gtc-build -j"$(nproc)" > /dev/null \
    && install -m755 /tmp/gtc-build/gamestream-test-client /usr/local/bin/ \
    && rm -rf /tmp/gtc /tmp/gtc-build \
    && gamestream-test-client --help > /dev/null
COPY moonshine-test-headless /usr/local/bin/moonshine-test-headless
COPY battery.json targets.json grafana/test-runs-queries.json /usr/share/moonshine-test-headless/
# The client's image of the same commit, for --client runs on another machine.
RUN echo "ghcr.io/porkloin/gamestream-test-client:sha-$GAMESTREAM_TEST_CLIENT_COMMIT" \
      > /usr/share/moonshine-test-headless/client-image
# The tools' versions, which every run records (CI passes this repository's commit).
ARG MTH_COMMIT=dev
ARG MOONSHINE_OTEL_VERSION
ARG FRAMESTAMP_VERSION
RUN jq -n --arg h "$MTH_COMMIT" --arg c "$GAMESTREAM_TEST_CLIENT_COMMIT" --arg o "$MOONSHINE_OTEL_VERSION" \
      --arg f "$FRAMESTAMP_VERSION" '{harness: $h, client: $c, otel: $o, framestamp: $f}' \
      > /usr/share/moonshine-test-headless/versions.json
# hdrneon (the hdrneon and hdrneon-cycle workloads), from its latest release, checked against its published checksums.
RUN cd /tmp && name=hdrneon-linux-x86_64 \
    && curl -fsSLO "https://github.com/porkloin/hdrneon/releases/latest/download/$name.tar.gz" \
    && curl -fsSLO https://github.com/porkloin/hdrneon/releases/latest/download/SHA256SUMS \
    && sha256sum -c --ignore-missing SHA256SUMS \
    && tar xzf "$name.tar.gz" && install -Dm755 "$name/hdrneon" /usr/local/bin/hdrneon \
    && rm -rf /tmp/hdrneon* /tmp/SHA256SUMS
# clickflash, the click-to-flash workload (--app clickflash), the same way.
RUN cd /tmp && name=clickflash-linux-x86_64 \
    && curl -fsSLO "https://github.com/porkloin/clickflash/releases/latest/download/$name.tar.gz" \
    && curl -fsSLO https://github.com/porkloin/clickflash/releases/latest/download/SHA256SUMS \
    && sha256sum -c --ignore-missing SHA256SUMS \
    && tar xzf "$name.tar.gz" && install -Dm755 "$name/clickflash" /usr/local/bin/clickflash \
    && rm -rf /tmp/clickflash* /tmp/SHA256SUMS
# framestamp, the test pattern the client checks every frame of (the framestamp-* workloads): the pinned release, as
# the client's picture check is the same release's pattern.
ARG FRAMESTAMP_VERSION
RUN cd /tmp && name=framestamp-linux-x86_64 \
    && curl -fsSLO "https://github.com/porkloin/framestamp/releases/download/$FRAMESTAMP_VERSION/$name.tar.gz" \
    && curl -fsSLO "https://github.com/porkloin/framestamp/releases/download/$FRAMESTAMP_VERSION/SHA256SUMS" \
    && sha256sum -c --ignore-missing SHA256SUMS \
    && tar xzf "$name.tar.gz" && install -Dm755 "$name/framestamp" /usr/local/bin/framestamp \
    && rm -rf /tmp/framestamp* /tmp/SHA256SUMS
# Prometheus, for moonshine-otel summary: it loads a run's telemetry into a throwaway one and asks it what the test
# runs board asks.
COPY --from=docker.io/prom/prometheus:v3.15.1 /bin/prometheus /usr/local/bin/prometheus
# Built at run time; it only reads Moonshine's statistics socket, so one build serves every Moonshine.
ARG MOONSHINE_OTEL_VERSION
RUN git clone --depth 1 --branch "$MOONSHINE_OTEL_VERSION" https://github.com/porkloin/moonshine-otel \
      /usr/share/moonshine-test-headless/moonshine-otel
RUN systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service

ENV NVIDIA_DRIVER_CAPABILITIES=all
# Intel's Vulkan driver keeps video encode behind this flag.
ENV ANV_DEBUG=video-encode

# `podman container runlabel run IMAGE -- --moonshine REF` runs this. --systemd=always lets the systemd user manager manage the
# container's cgroup; SELinux would otherwise need a host boolean for that, hence label=disable. Moonshine needs
# kcmp(2), which podman's seccomp profile only allows with SYS_PTRACE (inside the container's user namespace).
# No process limit: building and linking the tests in parallel goes past podman's default of 2048. The current folder
# is /results: each run writes its own folder there, and checkouts in it can be tested as ./NAME.
LABEL run="podman run --rm -it --tz=local --systemd=always --security-opt label=disable --uts=host --cap-add SYS_PTRACE --shm-size 1g --pids-limit -1 --device /dev/dri --group-add keep-groups -v moonshine-test-headless-cache:/cache -v \$PWD:/results -v /sys:/run/host-sys:ro -v /etc/os-release:/run/host-os-release:ro IMAGE"
LABEL run-nvidia="podman run --rm -it --tz=local --systemd=always --security-opt label=disable --uts=host --cap-add SYS_PTRACE --shm-size 1g --pids-limit -1 --device nvidia.com/gpu=all -v moonshine-test-headless-cache:/cache -v \$PWD:/results -v /sys:/run/host-sys:ro -v /etc/os-release:/run/host-os-release:ro IMAGE"
# Like run, streaming to a test client on another machine (--client USER@HOST): Moonshine's ports published so the
# client can reach it, on 57984-58010 so a Moonshine already running here keeps the default ports. SSH asks for
# the client machine's password on the terminal.
LABEL run-remote="podman run --rm -it --tz=local --systemd=always --security-opt label=disable --uts=host --cap-add SYS_PTRACE --shm-size 1g --pids-limit -1 --device /dev/dri --group-add keep-groups -v moonshine-test-headless-cache:/cache -v \$PWD:/results -v /sys:/run/host-sys:ro -v /etc/os-release:/run/host-os-release:ro -p 57984:57984/tcp -p 57989:57989/tcp -p 58010:58010/tcp -p 57998-58000:57998-58000/udp -p 58011:58011/udp IMAGE"
LABEL serve="podman run --rm -it --tz=local --systemd=always --security-opt label=disable --uts=host --cap-add SYS_PTRACE --shm-size 1g --pids-limit -1 --device /dev/dri --group-add keep-groups -v moonshine-test-headless-cache:/cache -v \$PWD:/results -v /sys:/run/host-sys:ro -v /etc/os-release:/run/host-os-release:ro -p 47984:47984/tcp -p 47989:47989/tcp -p 48010:48010/tcp -p 47998-48000:47998-48000/udp IMAGE --serve"

# catatonit as PID 1 reaps the session's processes and forwards signals.
ENTRYPOINT ["/usr/bin/catatonit", "--", "/usr/local/bin/moonshine-test-headless"]
