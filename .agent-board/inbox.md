# Inbox (needs the human)

## Open
(none blocking software work)

## Answered or deferred
1. **Lighting flicker:** deferred by the user (2026-11-17). Assume steady lighting. Last measurement: 52% of frames dimmed, worst 31% down, at 1.0 ms.
2. **Physical changes:** the user (2026-11-17) says no collector, no extra light and no moving conveyor will be added yet. All hardware-dependent work is parked on branch `hardware-pending` (`HARDWARE_PENDING.md`).
3. **Pacing:** keep building M2/M3 software against synthetic scenes before checkpoint 1.
4. **Frame budget:** the user agreed that not every frame needs processing (~10 or fewer frames per part is fine); the orchestrator adopted the advisor's plan (see `budget-proposal.md`).
5. **Next session (3):** runs on a different machine with no camera. The board was made git-tracked so it travels with the clone.
6. **Git:** M1 work was committed to the new `develop` branch (`3591145`); all work merges there, and `main` is untouched. Branches must be pushed to `origin` for the next machine to see them (the user was asked to confirm).
