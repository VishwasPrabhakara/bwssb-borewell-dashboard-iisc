"""KH flags with a separate, versioned dashboard inclusion policy.

No observations are removed or corrected. 'excluded' means excluded from the
default session analysis; metric eligibility remains independent of that label.

Policy v3: the device cumulative-yield counter is unreliable when integrated
step-by-step in the delivered ZIPs (it can sit stationary or emit spurious
small decreases), so it is no longer used to segment sessions. Sessions are
split only on a >30 min gap, and volume is reconstructed by integrating the
flow rate (flow_lpm x minutes / 1000), reset per session.

Policy v3.1 flow-missing fallback: when a session has NO flow readings at all
(KH stripped the Flow Rate column in the 05.10.26 cleanup), volume falls back
to yield_end - yield_start on the session endpoints. The step-by-step jiggle
that killed Policy v2 averages out across a session, so endpoint delta is
reliable (>99% positive) in audits. If the yield endpoints also don't give a
positive delta, no_volume is kept and the session is excluded.
"""
POLICY_VERSION = 3.1


def build_sessions(times, water, flow, yield_):
    if not times:
        return []
    boundaries = [0]
    for i in range(1, len(times)):
        if (times[i] - times[i - 1]).total_seconds() > 1800:
            boundaries.append(i)
    boundaries.append(len(times))
    result = []
    for a, end in zip(boundaries, boundaries[1:]):
        e = end - 1
        n = end - a
        missing_water = any(v is None for v in water[a:end])
        missing_yield = any(v is None for v in yield_[a:end])
        missing_flow = any(v is None for v in flow[a:end])
        steps = [abs(water[i] - water[i - 1]) for i in range(a + 1, end)
                 if water[i] is not None and water[i - 1] is not None]
        jumps = sum(v > 20 for v in steps)
        drawdown = water[e] - water[a] if water[e] is not None and water[a] is not None else None
        # Policy v3: volume reconstructed from flow.
        # Policy v3.1: fall back to yield endpoints when flow is entirely missing.
        volume = 0.0
        has_any_flow = False
        for i in range(a + 1, end):
            if flow[i - 1] is not None:
                has_any_flow = True
                minutes = (times[i] - times[i - 1]).total_seconds() / 60.0
                if minutes >= 0:
                    volume += flow[i - 1] * minutes / 1000.0
        if not has_any_flow:
            y_start = yield_[a] if a < len(yield_) else None
            y_end = yield_[e] if e < len(yield_) else None
            if y_start is not None and y_end is not None and y_end > y_start:
                volume = y_end - y_start
        reasons = []
        if n < 3:
            reasons.append('too_few_samples')
        if volume <= 0:
            reasons.append('no_volume')
        if jumps:
            reasons.append('sensor_relock_jump')
        if drawdown is not None and drawdown < 0:
            reasons.append('net_level_rise')
        # Missing measurements are additional dashboard checks, not KH rules.
        if missing_water:
            reasons.append('missing_water_level')
        if missing_yield:
            reasons.append('missing_yield')
        excluded = n < 3 or volume <= 0 or drawdown is None
        status = 'excluded' if excluded else ('flagged' if reasons else 'ok')
        volume_ok = n >= 2 and not missing_flow and volume > 0
        drawdown_ok = n >= 3 and not missing_water and not jumps and drawdown is not None and drawdown > 0
        result.append({
            'start': a, 'stop': e, 'n': n,
            'drawdown_ft': round(drawdown, 2) if drawdown is not None else None,
            'pumped_kl': round(volume, 2),
            'max_step_ft': round(max(steps, default=0), 2),
            'jump_count': jumps, 'status': status,
            'usable': status == 'ok', 'reasons': reasons,
            'eligible_volume': volume_ok, 'eligible_drawdown': drawdown_ok,
            'eligible_specific_capacity': volume_ok and drawdown_ok,
        })
    return result


def summarize(sessions):
    counts = {'total': len(sessions), 'ok': 0, 'flagged': 0, 'excluded': 0,
              'eligible_volume': 0, 'eligible_drawdown': 0,
              'eligible_specific_capacity': 0, 'jump_events': 0, 'by_reason': {}}
    for session in sessions:
        counts[session['status']] += 1
        counts['jump_events'] += session['jump_count']
        for key in ('eligible_volume', 'eligible_drawdown', 'eligible_specific_capacity'):
            counts[key] += int(session[key])
        for reason in session['reasons']:
            counts['by_reason'][reason] = counts['by_reason'].get(reason, 0) + 1
    return counts
