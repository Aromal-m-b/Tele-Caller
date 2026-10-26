"""Editable planning model. Capacity value is not guaranteed cash profit."""
import argparse
import json
import math


def estimate(*, calls=1000, minutes=4, characters=1800, input_tokens=6000,
             output_tokens=600, containment=.6, hourly_cost=12, fixed_cost=180,
             stt_rate=.0077, tts_rate=.030, input_rate=.50, output_rate=3.0):
    values = locals().copy()
    if any(not math.isfinite(float(v)) or v < 0 for v in values.values()) or containment > 1:
        raise ValueError("Inputs must be finite and nonnegative; containment must be between 0 and 1.")
    variable = minutes * stt_rate + characters / 1000 * tts_rate + input_tokens / 1e6 * input_rate + output_tokens / 1e6 * output_rate
    hours = calls * containment * minutes / 60
    capacity_value = hours * hourly_cost
    cost = calls * variable + fixed_cost
    contribution = containment * minutes / 60 * hourly_cost - variable
    return {"currency": "USD", "assumptions": values, "variable_cost_per_call": round(variable, 6),
        "monthly_variable_cost": round(calls * variable, 2), "monthly_total_cost": round(cost, 2),
        "potential_staff_hours_released": round(hours, 2), "potential_capacity_value": round(capacity_value, 2),
        "capacity_value_minus_operating_cost": round(capacity_value - cost, 2),
        "break_even_calls_at_assumed_containment": math.ceil(fixed_cost / contribution) if contribution > 0 else None,
        "notice": "Illustrative planning estimate, not measured savings or guaranteed profit. Excludes telephony, tax, setup and unbudgeted costs."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=1000)
    parser.add_argument("--minutes", type=float, default=4)
    parser.add_argument("--characters", type=int, default=1800)
    parser.add_argument("--input-tokens", type=int, default=6000)
    parser.add_argument("--output-tokens", type=int, default=600)
    parser.add_argument("--containment", type=float, default=.6)
    parser.add_argument("--hourly-cost", type=float, default=12)
    parser.add_argument("--fixed-cost", type=float, default=180)
    parser.add_argument("--stt-rate", type=float, default=.0077)
    parser.add_argument("--tts-rate", type=float, default=.030)
    parser.add_argument("--input-rate", type=float, default=.50)
    parser.add_argument("--output-rate", type=float, default=3.0)
    args = parser.parse_args()
    try:
        print(json.dumps(estimate(**vars(args)), indent=2))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
