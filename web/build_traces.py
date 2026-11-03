"""Build web/traces.json for the demo UI from the engine.

Every number in the output comes from the engine's own functions --
nothing is hand-typed:

- projectedOts[t]: engine.project.project_from_state on the live state of
  the night at minute t (the copilot's knowledge rule applies: a stoppage
  enters projections only once it has started).
- clearedPct[t][k]: items cleared through Pack for cutoff k by minute t,
  as a percent of that cutoff's item pool (engine.simulate state).
- cuts[].otsAtClose: realized on-time percent of the cutoff at the minute
  it closes; asserted equal to engine.simulate.run().per_cut_ots.
- panel.*: engine.recommend.recommend, run exactly as engine/demo.py runs
  it (pre-shift, and again from the live state at minute 165 with the
  pre-shift moves applied).
- areas.*: per-area, per-minute state of the same sweep, for the layer-2
  flow animation. queue/staged/staffPresent/staffEffective and
  packedUndispatched are read straight off engine.simulate.SimState.
  processedCumulative is not a native counter (only Pack has one,
  state.cleared): it is recovered exactly by mass conservation along the
  chain and asserted against Pack's native counter at every minute.
  Shipping never processes (rate 0); its processed series is dispatched
  mass = Pack-cleared minus packed-but-undispatched. linksBlocked[t]
  lists the chain links blocked during the step that starts at minute t,
  from scenario.stoppages -- the same test simulate._step applies.

Branches (layer 3, "play the lead"): the top-level series above are the
no-intervention baseline. traces["branches"] carries two more full
nights in the identical structure, each simulated by simulate.run() /
advance() with the real recommendation moves, so the UI can swap traces
at a decision point without changing code:

- "preshiftLadder": the night with the pre-shift ladder's moves applied
  (the same run engine/demo.py reports as "with pre-shift moves").
- "preshiftAndMidshiftLadder": the pre-shift moves plus the mid-shift
  ladder that recommend() produces at the detection minute (165) on that
  line. Its history is identical to "preshiftLadder" up to the decision
  minute (asserted sample by sample); its projectedOts uses the pre-shift
  moves before minute 165 and both ladders from 165 on, so the series is
  what the copilot could actually project at each minute -- unlike
  run()'s internal alerts for this move set, which would apply the
  minute-165 moves retroactively from minute 0.

The posting is NOT simulated in any branch, deliberately. recommend()
drafts it (PostingDraft: area, people, hours, reason), but the engine
has no way to run it: simulate.run()/advance() change staffing only
through Move relocations, and a Move always takes its people out of a
source node -- nobody arrives from outside the roster (SimState.transit
and extra_eff are fed exclusively by moves). PostingDraft declares no
arrival minute and no efficiency factor for posted staff, so any
arrival time or productivity we picked would be invented, and
recommend()'s own post-ladder projection (76.2%) is likewise
moves-only: the posting exists precisely because that projection still
falls short. The mid-shift branch therefore carries the drafted posting
as data (decision.posting) with postingSimulated = false, and its night
is the ladder-only night.

Run from the repo root:  python3 web/build_traces.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.demo import DETECTION_MINUTE, _clock
from engine.generate import release_schedule
from engine.model import LINKS, NODE_ORDER, OTS_TARGET, SHIFT_MINUTES, demo_scenario
from engine.project import project_from_state, project_preshift
from engine.recommend import recommend
from engine.simulate import ALERT_CHECK_EVERY, advance, initial_state, run


def _moves(rec) -> list[dict]:
    return [
        {
            "source": mv.source,
            "dest": mv.dest,
            "people": mv.people,
            "atMinute": mv.at_minute,
            "factor": mv.factor,
        }
        for mv in rec.moves
    ]


def _posting(rec) -> dict | None:
    if rec.posting is None:
        return None
    p = rec.posting
    return {"area": p.area, "people": p.people, "hours": p.hours, "reason": p.reason}


def _r1(x: float) -> float:
    return round(x, 1) + 0.0  # +0.0 normalizes -0.0


def _sweep_night(scenario, schedule, pools, moves, project_moves_at) -> dict:
    """Minute-by-minute sweep of one night defined by `moves`.

    At minute t the projection is project_from_state(state_t, scenario,
    project_moves_at(t)) -- the moves the copilot has committed to at
    that point of this line's timeline. Everything is cross-asserted
    against simulate.run() of the same move set.
    """
    state = initial_state(scenario)
    projected_raw: list[float] = []
    cleared_pct: list[list[float]] = []
    ots_at_close: dict[int, float] = {}
    # Per-area raw state, sampled at the same instants (state at minute t,
    # before the step t -> t+1). Unrounded; rounding happens on output.
    area_queue: dict[str, list[float]] = {name: [] for name in NODE_ORDER}
    area_staged: dict[str, list[float]] = {name: [] for name in NODE_ORDER}
    area_staff_present: dict[str, list[int]] = {name: [] for name in NODE_ORDER}
    area_staff_eff: dict[str, list[float]] = {name: [] for name in NODE_ORDER}
    packed_undispatched: list[float] = []
    cleared_cum: list[float] = []
    released_cum: list[float] = []
    rel_ptr = 0
    rel_sum = 0.0

    for t in range(SHIFT_MINUTES + 1):
        assert state.minute == t
        projected_raw.append(
            project_from_state(state, scenario, project_moves_at(t)).night_ots
        )
        cleared_pct.append(
            [
                round(100.0 * state.cleared[k] / pools[k], 1) if pools[k] > 0 else 100.0
                for k in range(len(pools))
            ]
        )
        # Orders released so far, per the state's own release pointer.
        while rel_ptr < state.next_release:
            rel_sum += schedule[rel_ptr].size
            rel_ptr += 1
        released_cum.append(rel_sum)
        cleared_cum.append(sum(state.cleared))
        packed_undispatched.append(state.packed_undispatched)
        for name in NODE_ORDER:
            area_queue[name].append(sum(state.queues[name]))
            area_staged[name].append(sum(state.buffers[name]))
            area_staff_present[name].append(state.present[name])
            area_staff_eff[name].append(state.effective_staff(name))
        for k, cut in enumerate(scenario.cuts):
            if cut.minute == t:
                ots_at_close[k] = 100.0 * state.ontime[k] / pools[k]
        if t < SHIFT_MINUTES:
            advance(state, scenario, schedule, moves, t + 1)

    projected = [round(v, 1) for v in projected_raw]

    # --- cross-check against the engine's own full-run result ------------
    result = run(scenario, moves=moves)
    for k in range(len(pools)):
        assert abs(ots_at_close[k] - result.per_cut_ots[k]) < 0.05, (
            f"cut {k}: swept {ots_at_close[k]} vs run() {result.per_cut_ots[k]}"
        )
    assert abs(projected[SHIFT_MINUTES] - result.night_ots) < 0.05

    # --- per-area series (layer 2) -----------------------------------------
    # Cumulative processed per node by mass conservation: what has entered
    # a node and is no longer sitting in its queue has been processed by
    # it; what it processed but could not pass on sits in its out-buffer
    # (staged). Asserted below against every native record the engine
    # keeps, so the derivation cannot silently drift from the simulation.
    processed_cum: dict[str, list[float]] = {}
    flow_in = released_cum  # cumulative mass that has entered the chain
    for name in NODE_ORDER[:-1]:  # the four processing nodes, upstream first
        processed_cum[name] = [
            flow_in[t] - area_queue[name][t] for t in range(SHIFT_MINUTES + 1)
        ]
        flow_in = [
            processed_cum[name][t] - area_staged[name][t]
            for t in range(SHIFT_MINUTES + 1)
        ]
    # Shipping never processes: completed = dispatched = cleared - held.
    processed_cum["Shipping"] = [
        cleared_cum[t] - packed_undispatched[t] for t in range(SHIFT_MINUTES + 1)
    ]
    processed_min: dict[str, list[float]] = {
        name: [
            *(
                _r1(max(0.0, processed_cum[name][t + 1] - processed_cum[name][t]))
                for t in range(SHIFT_MINUTES)
            ),
            0.0,  # no step starts at minute 600
        ]
        for name in NODE_ORDER
    }
    links_blocked = {
        f"{a}->{b}": [
            any(s.link == (a, b) and s.active_at(t) for s in scenario.stoppages)
            for t in range(SHIFT_MINUTES + 1)
        ]
        for a, b in LINKS
    }

    # Cross-assertions against the engine's own full-run records.
    for t in range(SHIFT_MINUTES + 1):
        # The conservation-derived Pack total must equal Pack's native
        # cleared counter at every minute.
        assert abs(processed_cum["Pack"][t] - cleared_cum[t]) < 0.05, t
        for name in NODE_ORDER:
            # queue + staged is exactly what run() records as queue_trace.
            assert (
                abs(
                    area_queue[name][t]
                    + area_staged[name][t]
                    - result.queue_trace[name][t]
                )
                < 0.05
            ), (name, t)
            assert abs(area_staff_eff[name][t] - result.staff_trace[name][t]) < 1e-9, (
                name,
                t,
            )
    assert abs(released_cum[SHIFT_MINUTES] - sum(pools)) < 0.05
    assert abs(processed_cum["Pack"][SHIFT_MINUTES] - sum(result.cleared)) < 0.05
    # The Pack -> Shipping link reopens at 465, so by minute 600 everything
    # Pack cleared has also been dispatched.
    assert abs(processed_cum["Shipping"][SHIFT_MINUTES] - sum(result.cleared)) < 0.05

    areas = {
        "order": list(NODE_ORDER),
        "queue": {
            name: [_r1(v) for v in area_queue[name]] for name in NODE_ORDER
        },
        "staged": {
            name: [_r1(v) for v in area_staged[name]] for name in NODE_ORDER
        },
        "processedCumulative": {
            name: [_r1(v) for v in processed_cum[name]] for name in NODE_ORDER
        },
        "processedPerMinute": processed_min,
        "staffPresent": area_staff_present,
        "staffEffective": {
            name: [_r1(v) for v in area_staff_eff[name]] for name in NODE_ORDER
        },
        "packedUndispatched": [_r1(v) for v in packed_undispatched],
        "linksBlocked": links_blocked,
    }

    return {
        "projected": projected,
        "projectedRaw": projected_raw,
        "clearedPct": cleared_pct,
        "otsAtClose": ots_at_close,
        "areas": areas,
        "result": result,
        "processedCum": processed_cum,
        "areaStaged": area_staged,
        "packedUndispatched": packed_undispatched,
    }


def _cuts_out(scenario, pools, ots_at_close) -> list[dict]:
    return [
        {
            "label": cut.label,
            "minute": cut.minute,
            "items": int(round(pools[k])),
            "otsAtClose": round(ots_at_close[k], 1),
        }
        for k, cut in enumerate(scenario.cuts)
    ]


def _alert_summary(projected_raw: list[float]) -> tuple[int | None, int]:
    """(first alert minute, number of alert checks) of a projected series,
    on run()'s own cadence: a check every ALERT_CHECK_EVERY minutes, an
    alert when the projection is below the target. Computed from the
    emitted series so a branch's alerts reflect what the copilot could
    know at each minute of that line (see the module docstring)."""
    checks = [
        t
        for t in range(0, SHIFT_MINUTES, ALERT_CHECK_EVERY)
        if projected_raw[t] < OTS_TARGET
    ]
    return (checks[0] if checks else None), len(checks)


def _branch_out(sweep: dict, scenario, pools) -> dict:
    first_alert, n_checks = _alert_summary(sweep["projectedRaw"])
    return {
        "projectedOts": sweep["projected"],
        "clearedPct": sweep["clearedPct"],
        "areas": sweep["areas"],
        "cuts": _cuts_out(scenario, pools, sweep["otsAtClose"]),
        "outcome": {
            "nightOts": round(sweep["result"].night_ots, 1),
            "firstAlertMinute": first_alert,
            "firstAlertClock": _clock(first_alert or 0),
            "alertChecks": n_checks,
        },
    }


def main() -> None:
    scenario = demo_scenario()
    schedule = release_schedule(scenario)
    pools = [0.0] * len(scenario.cuts)
    for order in schedule:
        pools[order.cut_index] += order.size

    # --- copilot ladders, exactly as engine/demo.py runs them ------------
    rec_pre = recommend(scenario)
    live_state = initial_state(scenario)
    advance(live_state, scenario, schedule, rec_pre.moves, DETECTION_MINUTE)
    live_projection = project_from_state(live_state, scenario, rec_pre.moves)
    rec_live = recommend(scenario, live_state.clone())

    # --- baseline: the no-intervention night ------------------------------
    base = _sweep_night(scenario, schedule, pools, (), lambda t: ())
    assert abs(base["projected"][0] - project_preshift(scenario).night_ots) < 0.05
    result = base["result"]

    # --- branch A: pre-shift ladder applied -------------------------------
    sweep_a = _sweep_night(
        scenario, schedule, pools, rec_pre.moves, lambda t: rec_pre.moves
    )
    # The branch's curve must be the ladder's own numbers: the pre-shift
    # projection after moves at minute 0, and the demo's live projection
    # once the stoppage is detected.
    assert abs(sweep_a["projected"][0] - rec_pre.projected.night_ots) < 0.05
    assert (
        abs(sweep_a["projected"][DETECTION_MINUTE] - live_projection.night_ots)
        < 0.05
    )
    # All of branch A's moves are committed at minute 0, so the alert
    # summary of its emitted series must match run()'s internal alerts.
    first_a, checks_a = _alert_summary(sweep_a["projectedRaw"])
    assert first_a == sweep_a["result"].first_alert_minute
    assert checks_a == len(sweep_a["result"].alerts)
    assert abs(sweep_a["projectedRaw"][first_a] - sweep_a["result"].alerts[0][1]) < 0.05

    # --- branch B: pre-shift + mid-shift ladders (posting not simulated) --
    all_moves = rec_pre.moves + rec_live.moves
    sweep_b = _sweep_night(
        scenario,
        schedule,
        pools,
        all_moves,
        lambda t: rec_pre.moves if t < DETECTION_MINUTE else all_moves,
    )
    # From the detection minute the branch's projection is the mid-shift
    # ladder's own projected value (moves only -- no posting effect).
    assert (
        abs(sweep_b["projected"][DETECTION_MINUTE] - rec_live.projected.night_ots)
        < 0.05
    )
    # Shared history: identical states and projections up to the decision.
    # (processedPerMinute[165] is the first sample that may differ: it is
    # the step that starts at 165, the first step the new moves touch.)
    for t in range(DETECTION_MINUTE + 1):
        assert sweep_b["clearedPct"][t] == sweep_a["clearedPct"][t], t
        for key in ("queue", "staged", "processedCumulative", "staffEffective"):
            for name in NODE_ORDER:
                assert sweep_b["areas"][key][name][t] == sweep_a["areas"][key][name][t], (
                    key,
                    name,
                    t,
                )
        for name in NODE_ORDER:
            assert (
                sweep_b["areas"]["staffPresent"][name][t]
                == sweep_a["areas"]["staffPresent"][name][t]
            ), (name, t)
        assert (
            sweep_b["areas"]["packedUndispatched"][t]
            == sweep_a["areas"]["packedUndispatched"][t]
        ), t
    for t in range(DETECTION_MINUTE):
        assert sweep_b["projected"][t] == sweep_a["projected"][t], t
        for name in NODE_ORDER:
            assert (
                sweep_b["areas"]["processedPerMinute"][name][t]
                == sweep_a["areas"]["processedPerMinute"][name][t]
            ), (name, t)
    assert sweep_b["areas"]["linksBlocked"] == sweep_a["areas"]["linksBlocked"]

    branch_a = _branch_out(sweep_a, scenario, pools)
    branch_a.update(
        {
            "label": "Pre-shift ladder applied",
            "decision": {
                "minute": 0,
                "clock": _clock(0),
                "kind": "preshift",
                "projectionBefore": round(rec_pre.baseline.night_ots, 1),
                "projectionAfter": round(rec_pre.projected.night_ots, 1),
                "bindingNode": rec_pre.binding_node,
                "moves": _moves(rec_pre),
                "posting": _posting(rec_pre),
            },
        }
    )
    branch_b = _branch_out(sweep_b, scenario, pools)
    branch_b.update(
        {
            "label": "Pre-shift ladder + mid-shift ladder (posting drafted, not simulated)",
            "decision": {
                "minute": DETECTION_MINUTE,
                "clock": _clock(DETECTION_MINUTE),
                "kind": "midshift",
                "projectionBefore": round(rec_live.baseline.night_ots, 1),
                "projectionAfter": round(rec_live.projected.night_ots, 1),
                "bindingNode": rec_live.binding_node,
                "moves": _moves(rec_live),
                "posting": _posting(rec_live),
                "postingSimulated": False,
                "postingNote": (
                    "Drafted by recommend() but not simulated: the engine "
                    "changes staffing only through Move relocations (a "
                    "source node always loses the people), and PostingDraft "
                    "declares no arrival minute or efficiency for posted "
                    "staff. This branch's night is the ladder-only night; "
                    "its 76.2% close is recommend()'s own moves-only "
                    "projection, realized."
                ),
            },
        }
    )

    planned_total = sum(n.planned_staff for n in scenario.nodes)
    present_total = planned_total - sum(dict(scenario.absences).values())

    traces = {
        "meta": {
            "volume": scenario.volume,
            "plannedStaff": planned_total,
            "presentStaff": present_total,
            "absences": [
                {"area": name, "count": n} for name, n in scenario.absences
            ],
            "otsTarget": 99.0,
            "shiftStart": "17:30",
            "shiftEnd": "03:30",
            "shiftMinutes": SHIFT_MINUTES,
            "baseline": "no intervention",
            "source": "engine/project.py + engine/simulate.py + engine/recommend.py",
        },
        "projectedOts": base["projected"],
        "clearedPct": base["clearedPct"],
        "areas": base["areas"],
        "cuts": _cuts_out(scenario, pools, base["otsAtClose"]),
        "stoppages": [
            {
                "from": s.link[0],
                "to": s.link[1],
                "start": s.start,
                "end": s.end,
                "startClock": _clock(s.start),
                "endClock": _clock(s.end),
            }
            for s in scenario.stoppages
        ],
        "markers": [
            {"minute": 0, "kind": "decision", "label": "Pre-shift decision"},
            {
                "minute": DETECTION_MINUTE,
                "kind": "decision",
                "label": "Stoppage detected",
            },
        ],
        "panel": {
            "preshift": {
                "projection": round(rec_pre.baseline.night_ots, 1),
                "bindingNode": rec_pre.binding_node,
                "moves": _moves(rec_pre),
                "afterMoves": round(rec_pre.projected.night_ots, 1),
                "posting": _posting(rec_pre),
            },
            "midshift": {
                "minute": DETECTION_MINUTE,
                "clock": _clock(DETECTION_MINUTE),
                "liveProjection": round(live_projection.night_ots, 1),
                "bindingNode": rec_live.binding_node,
                "moves": _moves(rec_live),
                "baseline": round(rec_live.baseline.night_ots, 1),
                "afterMoves": round(rec_live.projected.night_ots, 1),
                "posting": _posting(rec_live),
            },
        },
        "outcome": {
            "nightOts": round(result.night_ots, 1),
            "withPreshiftMovesNightOts": round(sweep_a["result"].night_ots, 1),
            "firstAlertMinute": result.first_alert_minute,
            "firstAlertClock": _clock(result.first_alert_minute or 0),
            "alertChecks": len(result.alerts),
        },
        "branches": {
            "preshiftLadder": branch_a,
            "preshiftAndMidshiftLadder": branch_b,
        },
    }

    out = Path(__file__).with_name("traces.json")
    out.write_text(json.dumps(traces, indent=1), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    projected = base["projected"]
    print(f"projected[0]={projected[0]}  projected[165]="
          f"{projected[DETECTION_MINUTE]}  projected[600]={projected[SHIFT_MINUTES]}")
    print(f"night OTS (run) = {result.night_ots:.1f}%, first alert at minute "
          f"{result.first_alert_minute}, {len(result.alerts)} alert checks")
    print("areas processed by minute 600: " + ", ".join(
        f"{name} {base['processedCum'][name][SHIFT_MINUTES]:,.0f}" for name in NODE_ORDER
    ))
    print(f"peak staged at Picking (stoppage 1) = "
          f"{max(base['areaStaged']['Picking']):,.0f} items; peak packed-undispatched "
          f"(stoppage 2) = {max(base['packedUndispatched']):,.0f} items")
    print(f"branch preshiftLadder: projected[0]={sweep_a['projected'][0]}  "
          f"projected[150]={sweep_a['projected'][150]}  "
          f"projected[165]={sweep_a['projected'][DETECTION_MINUTE]}  "
          f"night OTS = {sweep_a['result'].night_ots:.1f}%")
    print(f"branch preshiftAndMidshiftLadder: projected[165]="
          f"{sweep_b['projected'][DETECTION_MINUTE]}  "
          f"night OTS = {sweep_b['result'].night_ots:.1f}%  "
          f"(posting drafted, not simulated)")


if __name__ == "__main__":
    main()
