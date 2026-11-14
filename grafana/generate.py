#!/usr/bin/env python3
"""Generate the test runs board (test-runs.json) and its queries for moonshine-otel summary (test-runs-queries.json).

    python3 grafana/generate.py

Builds on moonshine-otel's dashboard generator (its panels and layout): from the checkout at MOONSHINE_OTEL, or
the release the Containerfile pins, cloned into ~/.cache/moonshine-test-headless. Edit this file rather than the JSON.
"""

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS = HERE.parent / "targets.json"


def pinned_moonshine_otel():
    """The moonshine-otel release the Containerfile pins, cloned once into the cache."""
    version = re.search(r"^ARG MOONSHINE_OTEL_VERSION=(\S+)$", (HERE.parent / "Containerfile").read_text(), re.M)[1]
    path = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "moonshine-test-headless" / f"moonshine-otel-{version}"
    if not path.exists():
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", version,
                        "https://github.com/porkloin/moonshine-otel", str(path)], check=True)
    return path


MOONSHINE_OTEL = Path(os.environ["MOONSHINE_OTEL"]) if os.environ.get("MOONSHINE_OTEL") else pinned_moonshine_otel()

_spec = importlib.util.spec_from_file_location(
    "moonshine_otel_dashboards", MOONSHINE_OTEL / "observability" / "grafana" / "generate.py")
_mo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mo)
CAPTURED, PROM, TEMPO = _mo.CAPTURED, _mo.PROM, _mo.TEMPO
Layout, dashboard, query_var, stat, target = _mo.Layout, _mo.dashboard, _mo.query_var, _mo.stat, _mo.target
STAGES = _mo.STAGES


# --------------------------------------------------------------------------------------------
# Test runs: a moonshine-test-headless run's streams, scored against the targets
# --------------------------------------------------------------------------------------------

# Which workloads a run can have, and which measurements apply to each.
BATTERY = HERE.parent / "battery.json"
DEFAULT_MEASURES = ["correctness", "smoothness", "server_latency", "loss_recovery", "bitrate"]


def whole(metric, sel):
    """A finished stream's cumulative series: its last value over the range."""
    return f"last_over_time({metric}{{{sel}}}[$__range])"


def host(metric, extra, sel):
    """Moonshine's series for the client's streams that `sel` picks, joined by the stream key."""
    return (f'({whole(metric, extra)} and on (stream_key) group by (stream_key) '
            f'({whole("moonshine_client_frames_total", sel)}))')


CLEAN = ', stream="clean"'
# Streams with nothing dropped on purpose: clean and click streams.
UNDROPPED = ', stream=~"clean|click"'
HIGH_LOAD = CLEAN + ', load="high"'
VSYNC = ', mode="vsync"'


def sent_fps(sel):
    """Per stream, the frame rate Moonshine sent at: one over its median frame interval. The median, not the mean, so a
    workload's loading frames (a frame a second until GravityMark renders) don't drag it down; and what Moonshine
    sent, not the stream's target, so a game the GPU can't run at the target isn't counted against delivery."""
    return f'(1 / histogram_quantile(0.5, sum by (stream_key) ({host("moonshine_frame_interval_seconds", "", sel)})))'


def per_side_fps(sel):
    """The frame rate Moonshine sent at, averaged over the side's streams, as a scalar: frame intervals for each side
    at its own rate, so two runs at different modes compare."""
    return f'scalar(avg({sent_fps(sel)}))'


def delivered(sel):
    """The average stream's share of Moonshine's frames that reached the client, in percent: received over received
    plus missing (frame numbers that never arrived), both after the client's warmup. Counted, not estimated from frame
    rates, and per stream, so streams at different rates don't blur together."""
    received = f'sum by (stream_key) ({whole("moonshine_client_frames_total", sel)})'
    missing = f'(sum by (stream_key) ({whole("moonshine_client_frames_missing_total", sel)}) or 0 * {received})'
    return f'100 * avg({received} / ({received} + {missing}))'


def switching(sel):
    """The streams whose captured content changed color space, by stream key."""
    return f'({host("moonshine_content_switches_total", "", sel)} > 0)'


def clean_switching(sel):
    """The switching streams that lost no frames, so every keyframe in them was Moonshine's own."""
    lossy = f'(sum by (stream_key) ({whole("moonshine_client_frames_missing_total", sel)}) > 0)'
    return f'({switching(sel)} unless on (stream_key) {lossy})'


# Empty when run B is run A, so B's series vanish and one run shows as one number, one set of bars, one line.
DIFFERENT_RUNS = ('label_replace(vector(1), "r", "$run/$side", "", "") unless on (r) '
                  'label_replace(vector(1), "r", "$run_b/$side_b", "", "")')
# A and B are each a run and a side of it: a run with a baseline holds both builds' streams (side="base" and "test").
A_SEL, B_SEL = 'run="$run", side="$side"', 'run="$run_b", side="$side_b"'


def only_if_compared(expr):
    return f"({expr}) and on () ({DIFFERENT_RUNS})"

# Each scored metric as PromQL over the client's streams that `sel` picks (Moonshine's are joined to them), in the
# metric's unit in targets.json: frame intervals multiply seconds by the frame rate Moonshine sent at (per stream, or the side's average).
SCORED = {
    # 0 only when there were streams to count in: a side without them is "not measured", not a clean score.
    "bad_pictures": lambda sel: f'sum({whole("moonshine_client_frames_total", sel + ", result!=\"ok\"")}) or 0 * sum({whole("moonshine_client_frames_total", sel)})',
    "missing_clean": lambda sel: f'sum({whole("moonshine_client_frames_missing_total", sel + UNDROPPED)}) or 0 * sum({whole("moonshine_client_frames_total", sel + UNDROPPED)})',
    # Each stream's p99 in its own frame intervals (Moonshine's sending rate), then the median stream: pooling streams
    # at different rates mixes their intervals, and one stalled stream (lost frames and delivered rate show it) would
    # otherwise outweigh the rest.
    "frame_time_p99": lambda sel: (
        f'quantile(0.5, histogram_quantile(0.99, sum by (stream_key) ('
        f'{whole("moonshine_client_frame_interval_seconds", sel + CLEAN)})) * {sent_fps(sel + CLEAN)})'),
    "fps_delivered": lambda sel: delivered(sel + CLEAN),
    # Not the loss streams: their extra keyframes would make the tail the keyframes' encode time.
    "server_p99_ms": lambda sel: f'1000 * histogram_quantile(0.99, sum({host("moonshine_frame_duration_seconds", CAPTURED, sel + ", stream!=\"loss\"")}))',
    "freeze_p95": lambda sel: f'histogram_quantile(0.95, sum({whole("moonshine_client_freeze_duration_seconds", sel)})) * {per_side_fps(sel)}',
    "recovered_without_keyframe": lambda sel: (
        f'100 * (histogram_count(sum({whole("moonshine_client_freeze_duration_seconds", sel + ", recovered_by=\"recovery_frame\"")})) or vector(0))'
        f' / histogram_count(sum({whole("moonshine_client_freeze_duration_seconds", sel)}))'),
    "click_median": lambda sel: f'histogram_quantile(0.5, sum({whole("moonshine_client_click_duration_seconds", sel)})) * {per_side_fps(sel)}',
    # Both from Moonshine, after watch's warmup (which holds each stream's opening keyframe), on the clean streams
    # whose content switched and that lost nothing: a keyframe the client asked for to recover isn't a switch's.
    "keyframes_per_switch": lambda sel: (
        f'sum({host("moonshine_frames_total", "frame_kind=\"key\"", sel + CLEAN)} and on (stream_key) {clean_switching(sel + CLEAN)})'
        f' / (sum({clean_switching(sel + CLEAN)}) > 0)'),
    # The client's model of a display presenting with V-Sync (moonlight-qt's default), on the clean streams.
    "display_latency_p99": lambda sel: f'1000 * histogram_quantile(0.99, sum({whole("moonshine_client_display_latency_seconds", sel + CLEAN + VSYNC)}))',
    "display_dropped": lambda sel: (
        f'100 * (sum({whole("moonshine_client_display_frames_total", sel + CLEAN + VSYNC + ", shown=\"false\"")})'
        f' or 0 * sum({whole("moonshine_client_display_frames_total", sel + CLEAN + VSYNC)}))'
        f' / sum({whole("moonshine_client_display_frames_total", sel + CLEAN + VSYNC)})'),
    "display_judder": lambda sel: (
        f'100 * (sum({whole("moonshine_client_display_judder_total", sel + CLEAN + VSYNC)})'
        f' or 0 * sum({whole("moonshine_client_display_frames_total", sel + CLEAN + VSYNC)}))'
        f' / sum({whole("moonshine_client_display_frames_total", sel + CLEAN + VSYNC + ", shown=\"true\"")})'),
    "load_fps": lambda sel: delivered(sel + HIGH_LOAD),
    "peak_bitrate": lambda sel: (
        f'100 * max({whole("moonshine_client_bitrate_peak_bit_per_second", sel + CLEAN)})'
        f' / max({whole("moonshine_client_bitrate_requested_bit_per_second", sel + CLEAN)})'),
}
# How a tile shows a targets.json unit: Grafana's unit, decimals, and the words for the thresholds.
TILE_UNITS = {"frame intervals": ("suffix: frames", 2, " frames"), "ms": ("ms", 2, " ms"), "%": ("percent", 1, "%"),
              "% of requested": ("percent", 1, "%"), "": ("none", 0, "")}
# A run's facts (moonshine.test.info labels) in the order and words the Setup tables show them: the host's and the
# client's are the same fields.
FACTS = {"machine": "Machine", "cpu": "CPU", "memory": "Memory", "gpu": "GPU", "driver": "Driver",
         "other_gpus": "Other GPUs", "os": "OS", "kernel": "Kernel", "network": "Network",
         "code": "Build", "ref": "Source", "commit": "Commit", "baseline": "Baseline", "route": "Route",
         "resolution": "Resolution", "fps": "Frame rate", "bitrate": "Bitrate", "seconds": "Stream length", "chaos": "Chaos", "tier": "Battery",
         "harness_version": "moonshine-test-headless", "client_version": "gamestream-test-client", "otel_version": "moonshine-otel"}
# Run A and run B: blue and orange, apart for color-blind readers too; named by their code (a_id, b_id).
A_COLOR, B_COLOR, NO_DATA_COLOR = "#3987e5", "#d95926", "#5a5f66"
# Moonshine's frame stages in pipeline order, a color each; A's blue and B's orange go to the two thinnest.
STAGE_COLORS = dict(zip(STAGES, ["#008300", "#d55181", "#c98500", "#199e70", "#9085e9", "#e66767", "#3987e5", "#d95926"]))
A_NAME, B_NAME = "${a_id} · A", "${b_id} · B"
RUN_COLORS = [
    {"matcher": {"id": "byRegexp", "options": ".* · A$"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": A_COLOR}}]},
    {"matcher": {"id": "byRegexp", "options": ".* · B$"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": B_COLOR}}]},
]
# Unitless metrics that aren't counts.
DECIMALS = {"keyframes_per_switch": 2, "display_dropped": 2, "display_judder": 2}
# What an empty tile means.
NO_VALUE = {"freeze_p95": "no loss test", "recovered_without_keyframe": "no loss test", "click_median": "no click test",
            "keyframes_per_switch": "no HDR switch test", "load_fps": "no GPU-load test",
            "display_latency_p99": "no display model", "display_dropped": "no display model",
            "display_judder": "no display model"}


def bands(m):
    """The target and acceptable values as threshold colors: good at or past the target, poor past acceptable. A metric
    without them yet is shown in one neutral color, not judged."""
    if m["target"] is None:
        return [{"color": "text", "value": None}]
    if m["better"] == "higher":
        return [{"color": "red", "value": None}, {"color": "yellow", "value": m["acceptable"]},
                {"color": "green", "value": m["target"]}]
    nudge = 1e-9
    return [{"color": "green", "value": None}, {"color": "yellow", "value": m["target"] + nudge},
            {"color": "red", "value": m["acceptable"] + nudge}]


def describe(m):
    """What a tile shows, and its thresholds. Why they're set where they are is in targets.json."""
    if m["target"] is None:
        return f"{m['what']}\n\nNot judged yet: {m['basis']}"
    op = "≥" if m["better"] == "higher" else "≤"
    unit = TILE_UNITS.get(m["unit"], ("", 0, " " + m["unit"]))[2]
    return (f"{m['what']}\n\nGreen {op} {m['target']}{unit}, yellow {op} {m['acceptable']}{unit}, red beyond. "
            "The thresholds are in targets.json.")


def hidden_number(name, expr):
    """A hidden variable holding a PromQL expression's value as a whole number."""
    return {"name": name, "type": "query", "datasource": PROM, "hide": 2, "refresh": 2,
            "query": {"query": f"query_result({expr})", "refId": name}, "definition": f"query_result({expr})",
            "regex": "/\\} (-?[0-9]+)/"}


def test_runs():
    targets = json.loads(TARGETS.read_text())["metrics"]
    battery = json.loads(BATTERY.read_text())["workloads"] if BATTERY.exists() else {}
    if not battery:
        print(f"no battery at {BATTERY}: the test-runs board gets no workload sections")
    L = Layout()
    p = []
    run_sel = {"A": A_SEL + ', codec=~"$codec"', "B": B_SEL + ', codec=~"$codec"'}
    names = {"A": A_NAME, "B": B_NAME}

    # --- pieces ---------------------------------------------------------------------------------------------------

    def tile(key, m, sides, extra=""):
        """A metric for run A (colored by its band), or A and B beside each other."""
        unit, decimals, _ = TILE_UNITS.get(m["unit"], ("none", 2, ""))
        panel = stat(m["label"][0].upper() + m["label"][1:], "", unit, describe(m), bands(m), DECIMALS.get(key, decimals))
        # A and B by their commits: the code's full name is in the Setup tables and the row titles.
        short = {"A": "${a_commit} · A", "B": "${b_commit} · B"}
        # Beside A, B only shows when it's another run; on its own (run B's report card) it always does.
        guard = only_if_compared if len(sides) > 1 else (lambda e: e)
        panel["targets"] = [target(SCORED[key](run_sel[side] + extra) if side == "A" else guard(SCORED[key](run_sel[side] + extra)),
                                   short[side] if len(sides) > 1 else "", side, instant=True)
                            for side in sides]
        empty = NO_VALUE.get(key, "not measured")
        panel["fieldConfig"]["defaults"]["noValue"] = empty
        # Nothing measured is grey, not a verdict.
        panel["fieldConfig"]["defaults"]["mappings"] = [
            {"type": "special", "options": {"match": "null+nan", "result": {"text": empty, "color": NO_DATA_COLOR, "index": 0}}}]
        compare = len(sides) > 1
        panel["options"].update({"colorMode": "value" if compare else "background", "justifyMode": "center",
                                 "textMode": "value_and_name" if compare else "value", "wideLayout": True,
                                 "orientation": "horizontal" if compare else "auto"})
        if compare:
            # The same size on every A/B tile, whatever the unit's length.
            panel["options"]["text"] = {"titleSize": 13, "valueSize": 24}
        return panel

    def facts(title, run, role, description, measured=()):
        """A run's moonshine.test.info sample for a role, as a list of facts; `measured` adds rows computed from the
        run's metrics, as (name, PromQL)."""
        extra = list(zip("BCDEFG", measured))
        info = f'last_over_time(moonshine_test_info{{run="{run}", role="{role}"}}[$__range])'
        return {
            "type": "table", "title": title, "description": description, "datasource": PROM,
            "targets": [dict(target(info, "", "A", instant=True, fmt="table"))]
                       + [dict(target(expr, "", ref, instant=True, fmt="table")) for ref, (_, expr) in extra],
            "transformations": [
                # The computed rows join the sample's row (one instant, one row each), before it's turned on its side.
                {"id": "merge", "options": {}},
                {"id": "filterFieldsByName", "options": {"exclude": {
                    "pattern": "^(Time|__name__|run|role|job|instance|otel_scope_.*|service_.*)$"}}},
                # The sample's value (1) goes first, so transpose keeps every fact as a row; its own row is dropped after.
                {"id": "organize", "options": {
                    "renameByName": {**FACTS, **{f"Value #{ref}": name for ref, (name, _) in extra}},
                    "indexByName": {"Value": 0, "Value #A": 0, **{k: i + 1 for i, k in enumerate(FACTS)},
                                    **{f"Value #{ref}": len(FACTS) + 1 + i for i, (ref, _) in enumerate(extra)}}}},
                {"id": "transpose", "options": {}},
                {"id": "filterByValue", "options": {"type": "exclude", "match": "any", "filters": [
                    {"fieldName": "Field", "config": {"id": "regex", "options": {"value": "^Value( #A)?$"}}}]}},
            ],
            "fieldConfig": {"defaults": {"custom": {"align": "left"}}, "overrides": [
                {"matcher": {"id": "byName", "options": "Field"}, "properties": [{"id": "custom.width", "value": 165}]}]},
            "options": {"showHeader": False, "cellHeight": "sm"},
        }

    def setup(run, into):
        sel = run_sel["A" if run == "$run" else "B"]
        measured = [
            ("Streams", f'count(count by (stream_key) ({whole("moonshine_client_frames_total", sel)}))'),
            ("Frames decoded", f'sum({whole("moonshine_client_frames_total", sel)})'),
            ("Clock sync (ms)", f'round(1000 * max({whole("moonshine_client_clock_uncertainty_seconds", sel)}), 0.01)'),
        ]
        for title, role, description, rows in [
            ("Host", "host", "The machine Moonshine ran on, from gamestream-test-client's probe. GPU and driver: the GPU it encoded on (Vulkan).", ()),
            ("Client", "client", "The machine the test client ran on, from its probe. GPU and driver: the GPU it decoded on (VAAPI).", ()),
            ("Build and stream", "run", "The build under test, the baseline when there was one, the route between the machines (never an address), "
             "the stream's settings, and what the run recorded. Clock sync: how closely the client's times are placed on "
             "Moonshine's clock (half the fastest NTP round trip).", measured),
        ]:
            into.append(L.place(facts(title, run, role, description, rows), 8, 13))

    quantiles = [("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99), ("p99.9", 0.999)]

    def bars(title, description, queries, by, unit="ms", decimals=None, rotate=0):
        """A and B side by side over a category (`by`): one instant table query per side."""
        return {
            "type": "barchart", "title": title, "description": description, "datasource": PROM,
            "targets": [dict(target(q if side == "A" else only_if_compared(q), "", side, instant=True, fmt="table"))
                        for side, q in queries],
            "transformations": [
                {"id": "joinByField", "options": {"byField": by, "mode": "outer"}},
                {"id": "organize", "options": {"excludeByName": {"Time": True, "Time 1": True, "Time 2": True},
                                               "renameByName": {f"Value #{side}": names[side] for side, _ in queries}}},
                {"id": "sortBy", "options": {"sort": [{"field": by}]}},
            ],
            "fieldConfig": {"defaults": {"unit": unit, **({"decimals": decimals} if decimals is not None else {})},
                            "overrides": RUN_COLORS},
            "options": {"xField": by, "orientation": "vertical", "groupWidth": 0.7, "barWidth": 0.9, "xTickLabelRotation": rotate,
                        "showValue": "never", "legend": {"displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi"}},
        }

    def distribution(title, description, metric, extra, wsel, on_host=False):
        """A and B's p50 to p99.9 of a histogram, over the whole run."""
        def rows(sel):
            series = host(metric, extra, sel) if on_host else whole(metric, sel + extra)
            return " or ".join(
                f'label_replace(1000 * histogram_quantile({q}, sum({series})), "q", "{n}", "", "")' for n, q in quantiles)
        return bars(title, description, [(side, rows(run_sel[side] + wsel)) for side in "AB"], "q")

    def histogram(title, description, metric, extra, wsel, bins, axis, log=False):
        """A and B's share of samples per bin, from a native histogram. bins: (x, low, high, only_if) with low and high
        in seconds as PromQL (high None for the open last bin, shown as "more"), and only_if a condition the bin
        needs to show, or None."""
        def rows(sel):
            h = f"sum({whole(metric, sel + extra)})"
            return " or ".join(
                f'label_replace(100 * histogram_fraction({lo}, {hi or "+Inf"}, {h}), "bin", "{x:g}", "", "")'
                + (f" and on () ({cond})" if cond else "")
                for x, lo, hi, cond in bins)
        more = bins[-1][0]
        return {
            "type": "barchart", "title": title, "description": description, "datasource": PROM,
            "targets": [dict(target(rows(run_sel[side] + wsel) if side == "A" else only_if_compared(rows(run_sel[side] + wsel)),
                                    "", side, instant=True, fmt="table")) for side in "AB"],
            "transformations": [
                {"id": "joinByField", "options": {"byField": "bin", "mode": "outer"}},
                {"id": "organize", "options": {"excludeByName": {"Time": True, "Time 1": True, "Time 2": True},
                                               "renameByName": {"bin": axis, "Value #A": A_NAME, "Value #B": B_NAME}}},
                {"id": "convertFieldType", "options": {"conversions": [{"targetField": axis, "destinationType": "number"}]}},
                {"id": "sortBy", "options": {"sort": [{"field": axis}]}},
            ],
            "fieldConfig": {
                "defaults": {"unit": "percent", "decimals": 1, "min": 0 if not log else None,
                             "custom": {"fillOpacity": 85, "lineWidth": 0,
                                        **({"scaleDistribution": {"type": "log", "log": 10}} if log else {})}},
                "overrides": RUN_COLORS + [{"matcher": {"id": "byName", "options": axis}, "properties": [
                    {"id": "unit", "value": "none"}, {"id": "decimals", "value": 1 if bins[1][0] - bins[0][0] < 1 else 0},
                    {"id": "mappings", "value": [{"type": "value", "options": {f"{more:g}": {"text": "more"}}}]}]}]},
            "options": {"xField": axis, "orientation": "vertical", "groupWidth": 0.85, "barWidth": 0.95, "showValue": "never",
                        "xTickLabelSpacing": 60, "legend": {"displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi"}},
        }

    def frame_bins(step, last):
        """Bins in frame intervals at the stream's frame rate: [0, step), ... up to `last`, then the rest."""
        n = round(last / step)
        return [(round(i * step, 3), f"{i * step:g} / $fps", f"{(i + 1) * step:g} / $fps", None) for i in range(n)] \
            + [(last, f"{last:g} / $fps", None, None)]

    def ms_bins(limit_ms=50, frames=2):
        """1 ms bins up to `frames` frame intervals (at most limit_ms), then the rest. Bins past the frame limit
        return nothing, so a fast stream's chart isn't mostly empty."""
        top = f"scalar(clamp_max(vector({frames} / $fps), {limit_ms / 1000}))"
        return [(i, f"{i / 1000:g}", f"{(i + 1) / 1000:g}", f"vector({frames} * 1000 / $fps) > {i}") for i in range(limit_ms)] \
            + [(999, top, None, None)]

    def stages(wsel):
        """Moonshine's mean time per stage, stacked in pipeline order: a bar per run."""
        def q(side, name):
            return (f'label_replace(1000 * histogram_avg(sum by (stage) ({host("moonshine_frame_stage_duration_seconds", CAPTURED + ", stage!=\"consumer_queue\"", run_sel[side] + wsel)})),'
                    f' "run", "{name}", "", "")')
        return {
            "type": "barchart", "title": "Host latency by stage",
            "description": "Moonshine's mean time per stage of a frame on the host, from capture to its last packet sent, in "
                           "pipeline order; the bar's length is the mean total. consumer_queue is part of encode_wait, so it's left out.",
            "datasource": PROM,
            "targets": [dict(target(f"{q('A', A_NAME)} or {only_if_compared(q('B', B_NAME))}", "", "A", instant=True, fmt="table"))],
            "transformations": [
                {"id": "groupingToMatrix", "options": {"columnField": "stage", "rowField": "run", "valueField": "Value"}},
                {"id": "organize", "options": {"renameByName": {"run\\stage": "run"},
                                               "indexByName": {"run\\stage": 0, **{st: i + 1 for i, st in enumerate(STAGES)}}}},
            ],
            "fieldConfig": {"defaults": {"unit": "ms", "decimals": 2, "custom": {"fillOpacity": 85, "lineWidth": 0}},
                            "overrides": [{"matcher": {"id": "byName", "options": st},
                                           "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                                          for st, c in STAGE_COLORS.items()]},
            "options": {"xField": "run", "orientation": "horizontal", "stacking": "normal", "barWidth": 0.5, "showValue": "never",
                        "legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        }

    # Through the stream: each run pinned to its own window (@ A's end, back over the longer run's length), B moved
    # onto A's (offset by the difference of their starts), on seconds since A's first frame.
    # Each workload's timelines span its own streams: its window in run A (a_start_<w>, a_end_<w>), the longer of its
    # two runs (len_<w>), and how far B's are from A's (shift_<w>).
    def window(wid):
        return re.sub(r"[^A-Za-z0-9]", "_", wid)

    def timeline(title, description, expr, unit, wid, reference=None):
        w = window(wid)
        pin = f"[${{len_{w}}}s:1s] @ ${{a_end_{w}}}"

        def shifted(e):
            return re.sub(r"\[(\d+s)\]", rf"[\1] offset ${{shift_{w}}}s", e)

        return {
            "type": "trend", "title": title, "datasource": PROM,
            "description": description + " Seconds since the workload's first frame in run A; a point a second, each over the 10 s before it.",
            "targets": [
                dict(target(f"vector(time() - ${{a_start_{w}}}){pin}", "seconds since start", "X", instant=True)),
                dict(target(f"({expr(run_sel['A'])}){pin}", A_NAME, "A", instant=True)),
                # The guard goes inside the subquery: a pinned range can't take part in `and`.
                dict(target(f"({only_if_compared(shifted(expr(run_sel['B'])))}){pin}", B_NAME, "B", instant=True)),
            ] + ([dict(target(f"({reference[1](run_sel['A'])}){pin}", reference[0], "R", instant=True))] if reference else []),
            "transformations": [
                {"id": "joinByField", "options": {"byField": "Time", "mode": "outer"}},
                {"id": "organize", "options": {"excludeByName": {"Time": True}}},
            ],
            # From zero: an auto-zoomed axis turns microseconds of noise into cliffs.
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "custom": {"lineWidth": 2, "showPoints": "auto", "spanNulls": True}},
                            "overrides": RUN_COLORS + [{"matcher": {"id": "byName", "options": "seconds since start"},
                                                        "properties": [{"id": "unit", "value": "suffix: s"}, {"id": "decimals", "value": 0}]}]
                            + ([{"matcher": {"id": "byName", "options": reference[0]}, "properties": [
                                {"id": "color", "value": {"mode": "fixed", "fixedColor": NO_DATA_COLOR}},
                                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}},
                                {"id": "custom.showPoints", "value": "never"}]}] if reference else [])},
            "options": {"xField": "seconds since start", "legend": {"displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi"}},
        }

    # Each measurement's panels for a workload's streams (wsel): its tiles, then its charts.
    def measure_panels(measure, wsel, wid):
        out = [tile(key, m, "AB", wsel) for key, m in targets.items() if m["measure"] == measure]
        if measure in ("smoothness", "load"):
            out.append(histogram("Frame pacing", "The time between frames arriving at the client, in frame intervals, clean "
                                 "streams: share of frames per tenth of a frame, on a log scale so stray frames show. A smooth "
                                 "stream is one bar at 1; bars near 0 are frames arriving bunched, bars at 2 and up are stutters.",
                                 "moonshine_client_frame_interval_seconds", CLEAN, wsel, frame_bins(0.1, 3), "frames", log=True))
            out.append(timeline("Frame pacing p99 over time", "The time between frames arriving at the client, p99, clean streams.",
                                lambda s: f'1000 * histogram_quantile(0.99, sum(rate(moonshine_client_frame_interval_seconds{{{s}{wsel}{CLEAN}}}[10s])))', "ms", wid))
            if measure == "smoothness":
                out.append(histogram("Decoded to on screen", "From the client decoding a frame to a display showing it, clean "
                                     "streams, in the client's model of moonlight-qt presenting with V-Sync: share of frames per "
                                     "millisecond. Even arrivals are one bar near half a refresh; bars a refresh or more further are "
                                     "frames queued behind late ones.",
                                     "moonshine_client_display_latency_seconds", CLEAN + VSYNC, wsel, ms_bins(), "ms"))
        elif measure == "server_latency":
            out.append(distribution("Host latency", "Moonshine's time on the host from capturing a frame to sending its last packet.",
                                    "moonshine_frame_duration_seconds", CAPTURED, wsel, on_host=True))
            out.append(stages(wsel))
        elif measure == "loss_recovery":
            out.append(histogram("Recovery time", "How long the picture stood still after a lost frame, until a frame recovered "
                                 "it, in frame intervals: share of freezes per frame.",
                                 "moonshine_client_freeze_duration_seconds", "", wsel, frame_bins(1, 10), "frames"))
        elif measure == "click_latency":
            clicks = histogram("Click to photon · $codec", "From the client sending a click to it decoding the flash the click "
                               "caused: share of clicks per millisecond, up to two frame intervals, then the rest. One chart per codec.",
                               "moonshine_client_click_duration_seconds", "", wsel, ms_bins(), "ms")
            clicks.update({"repeat": "codec", "repeatDirection": "h", "maxPerRow": 3})
            out.append(clicks)
            out.append(distribution("App latency", "The workload's share of a click: from the input arriving to its flash going to the GPU.",
                                    "moonshine_app_flash_duration_seconds", "", wsel))
        elif measure == "bitrate":
            out.append(timeline("Bitrate over time", "Bits the client received per second, mean, clean streams. Dashed: the bitrate asked for (run A).",
                                lambda s: f'histogram_avg(sum(rate(moonshine_client_bitrate_bit_per_second{{{s}{wsel}{CLEAN}}}[10s])))', "bps", wid,
                                reference=("requested", lambda s: f'max(moonshine_client_bitrate_requested_bit_per_second{{{s}{wsel}{CLEAN}}})')))
        return out

    # --- the board ------------------------------------------------------------------------------------------------

    sel_a = run_sel["A"]

    # Each run the same: its report card (tiles colored by their bands), then its machines and stream config in a row of
    # its own, to fold away on a short screen. Run B's fold until wanted.
    for side, run, name in [("A", "$run", "${a_id}"), ("B", "$run_b", "${b_id}")]:
        card = L.row(f"Report card · {side} · {name}")
        card["collapsed"] = side == "B"
        p.append(card)
        for key, m in targets.items():
            (card["panels"] if card["collapsed"] else p).append(L.place(tile(key, m, side), 4, 4))
        info = L.row(f"Machines + stream config · {side} · {name}")
        info["collapsed"] = side == "B"
        p.append(info)
        setup(run, info["panels"] if info["collapsed"] else p)

    # A section per workload: the measurements battery.json binds to it, A beside B. Folded; open the ones the runs had.
    for wid, w in battery.items():
        row = L.row(w.get("title", wid))
        row["collapsed"] = True
        p.append(row)
        wsel = f', workload="{wid}"'
        panels = [panel for measure in w.get("measures", DEFAULT_MEASURES) for panel in measure_panels(measure, wsel, wid)]
        # Measurements can share a panel (smoothness and load both chart pacing): once each.
        titles = set()
        panels = [panel for panel in panels if not (panel["title"] in titles or titles.add(panel["title"]))]
        # Its tiles in a line first, then its charts on lines of their own.
        # Lines of up to six tiles, each line filling the width so nothing floats up beside a short one.
        tiles = [panel for panel in panels if panel["type"] == "stat"]
        for i in range(0, len(tiles), 6):
            line = tiles[i:i + 6]
            for panel in line:
                row["panels"].append(L.place(panel, 24 // len(line), 5))
        L.wrap()
        for panel in [panel for panel in panels if panel["type"] != "stat"]:
            row["panels"].append(L.place(panel, 8, 8))

    p.append(L.row("Clock sync · A · ${a_id}"))
    p.append(L.place(stat(
        "Sync uncertainty", f'max({whole("moonshine_client_clock_uncertainty_seconds", sel_a)}) * 1000', "suffix: ms",
        "Half the fastest NTP round trip from the client to the host's machine: the client's times are on the host's "
        "clock to within this. Measured by gamestream-test-client --clock against clock-serve; nothing sets either clock.",
        None, 2), 8, 4))
    p.append(L.place(stat(
        "Clock offset", f'avg({whole("moonshine_client_clock_offset_seconds", sel_a + ", when=\"start\"")}) * 1000', "suffix: ms",
        "The host's clock less the client's, as measured; the client's times are shifted by it.", None, 2), 8, 4))
    p.append(L.place(stat(
        "Max drift per stream",
        f'max(abs({whole("moonshine_client_clock_offset_seconds", sel_a + ", when=\"end\"")}'
        f' - on (stream_key) {whole("moonshine_client_clock_offset_seconds", sel_a + ", when=\"start\"")})) * 1000',
        "suffix: ms", "How far the measured offset moved between a stream's start and end.", None, 3), 8, 4))

    p.append(L.row("Traces · A · ${a_id}"))
    keys = "${keys:regex}"
    for title, description, query, kind, width in [
        ("Freezes and injected drops", "A freeze's span sits in the trace of the frame that ended it; a dropped frame's trace has Moonshine's span and the client's marker.",
         f'{{ resource.stream.key =~ "{keys}" && name =~ "client: (freeze|dropped on purpose)" }} | select(span.frame.number, span.freeze.missing, span.freeze.recovered_by)', "spans", 12),
        ("Slowest frames", "Each trace is one frame: Moonshine's capture to send, the network, and the client's reassembly, wait and decode.",
         f'{{ resource.stream.key =~ "{keys}" && name = "client frame" }}', "traces", 12),
        ("Slowest clicks", "Each trace is one click: the client sending it, the workload's input to flash, and the client decoding the flash (in frame click.flash_frame).",
         f'{{ resource.stream.key =~ "{keys}" && name = "click" }} | select(span.click.id, span.click.flash_frame)', "traces", 24),
    ]:
        panel = {"type": "table", "title": title, "description": description, "datasource": TEMPO,
                 "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "tableType": kind, "limit": 50,
                              "spss": 50, "query": query}],
                 "fieldConfig": {"defaults": {}, "overrides": []}, "options": {"showHeader": True}}
        if kind == "traces":
            panel["options"]["sortBy"] = [{"displayName": "Duration", "desc": True}]
        p.append(L.place(panel, width, 12))

    def workload_window(wid):
        w, sel = window(wid), f'workload="{wid}"'
        start = 'min(min_over_time(moonshine_client_stream_start_seconds{{{run}, {sel}}}[$__range]))'
        end = 'max(max_over_time(moonshine_client_stream_end_seconds{{{run}, {sel}}}[$__range]))'
        a_start, b_start = start.format(run=A_SEL, sel=sel), start.format(run=B_SEL, sel=sel)
        a_end, b_end = end.format(run=A_SEL, sel=sel), end.format(run=B_SEL, sel=sel)
        # Each falls back to a number, so a run without this workload (or recorded before the stream start and end
        # gauges) leaves its timelines empty instead of their queries broken.
        return [
            hidden_number(f"a_start_{w}", f"floor({a_start}) or vector(0)"),
            hidden_number(f"a_end_{w}", f"ceil({a_end}) or vector(0)"),
            # The longer of the two runs' streams of this workload, so both fit.
            hidden_number(f"len_{w}", f"(ceil(max(label_replace({a_end} - {a_start}, \"r\", \"a\", \"\", \"\")"
                                      f" or label_replace({b_end} - {b_start}, \"r\", \"b\", \"\", \"\"))) + 5) or vector(5)"),
            # offset looks back, so a B that came after A gets a negative one.
            hidden_number(f"shift_{w}", f"(floor({a_start}) - floor({b_start})) or vector(0)"),
        ]

    def commit_of(name, run):
        v = query_var(name, "", f'label_values(moonshine_client_frames_total{{{run}}}, commit)', multi=False, include_all=False)
        v["hide"] = 2
        return v

    def code_of(name, run):
        v = query_var(name, "", f'label_values(moonshine_client_frames_total{{{run}}}, code)', multi=False, include_all=False)
        v["hide"] = 2
        return v

    keys_var = query_var("keys", "", 'label_values(moonshine_client_frames_total{' + A_SEL + '}, stream_key)')
    keys_var.pop("allValue")
    keys_var["hide"] = 2
    variables = [
        query_var("run", "Run A", "label_values(moonshine_client_frames_total, run)", multi=False, include_all=False),
        query_var("run_b", "Run B", "label_values(moonshine_client_frames_total, run)", multi=False, include_all=False),
        # The PR's build first for A, its baseline first for B, so a run with a baseline opens compared with it.
        {**query_var("side", "Side A", 'label_values(moonshine_client_frames_total{run="$run"}, side)', multi=False, include_all=False), "sort": 2},
        {**query_var("side_b", "Side B", 'label_values(moonshine_client_frames_total{run="$run_b"}, side)', multi=False, include_all=False), "sort": 1},
        query_var("codec", "Codec", 'label_values(moonshine_client_frames_total{run=~"$run|$run_b"}, codec)'),
        code_of("a_id", A_SEL),
        code_of("b_id", B_SEL),
        commit_of("a_commit", A_SEL),
        commit_of("b_commit", B_SEL),
        keys_var,
        # Each workload's window, for its timelines.
        *[v for wid in battery for v in workload_window(wid)],
        # Worked out from run A, not picked: a wrong one would misstate every frame-interval tile.
        {**query_var("fps", "Frame rate", 'label_values(moonshine_frames_total{stream_key=~"${keys:regex}"}, target_fps)',
                     multi=False, include_all=False), "hide": 2},
    ]
    d = dashboard(
        "moonshine-test-runs",
        "Moonshine test runs",
        "A moonshine-test-headless run (--traces) scored against targets.json, beside another run, workload by workload, with its frames in Tempo.",
        p, variables, time="now-7d")
    # Finished runs don't change.
    d["refresh"] = ""
    return d


def main():
    targets = json.loads(TARGETS.read_text())
    # The board's scored metrics as PromQL, for moonshine-otel summary: the same queries, so the board and the summary
    # can't disagree. `{sel}` is the client's series of a run; `$__range` and `$fps` are filled in. In targets.json's
    # order; a metric without a query is an error here.
    queries = {"metrics": [{"key": key, "expr": SCORED[key]("{sel}"), "empty": NO_VALUE.get(key, "not measured"),
                            "decimals": DECIMALS.get(key, TILE_UNITS.get(m["unit"], ("", 2))[1])}
                           for key, m in targets["metrics"].items()]}
    for name, d in [("test-runs-queries.json", queries), ("test-runs.json", test_runs())]:
        (HERE / name).write_text(json.dumps(d, indent=2) + "\n")
        print(f"wrote {HERE / name}")


if __name__ == "__main__":
    main()
