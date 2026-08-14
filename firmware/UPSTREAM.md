# Firmware provenance

This tree is **vendored** (copied in as regular files), not a submodule, so that a
fresh clone builds without a second fetch.

| | |
|---|---|
| Upstream | https://github.com/fra589/grbl-Mega-5X |
| Branch | `edge` |
| Fork point | `baaa1be` — *Update Copyright date and upgrade version to 1.2i* |
| License | GPLv3 (see `COPYING`) — modifications below are distributed under the same terms |

## Local modifications

Three files diverge from upstream. All three implement servo pen-lift on the
spindle PWM output, because this machine has no Z motor.

| File | Change |
|---|---|
| `grbl/config.h` | `PEN_SERVO_MODE`, `SERVO_MIN_US` / `SERVO_MAX_US` / `SERVO_PARK_US`, and `SPINDLE_PWM_ON_D6` |
| `grbl/cpu_map.h` | Spindle PWM retimed to a 50 Hz RC servo frame on D6 (Timer4, mode 14, /8 prescaler) |
| `grbl/spindle_control.c` | `spindle_stop()` and `spindle_set_speed()` park at the pen-up pulse instead of clearing the PWM enable bit |

That last one is the safety-critical one: stock grbl kills the PWM on `M5`, soft
reset, and ALARM, which leaves the servo limp and drops the pen onto the paper.
The patched version keeps the 50 Hz frame alive and parks at `SERVO_PARK_US`.

## Re-syncing with upstream

    git clone https://github.com/fra589/grbl-Mega-5X.git
    cd grbl-Mega-5X && git checkout edge
    # then re-apply the three files above

To recover the original clone's history on the machine this was vendored from,
rename `firmware/.git.disabled` back to `firmware/.git`.
