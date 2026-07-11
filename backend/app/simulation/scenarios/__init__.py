"""Scenario engine — pure functions of (engine, params).

Each scenario is a coroutine that takes the SimulationEngine and a
parameter dict, enqueues a deterministic sequence of ticks, and returns
a summary dict. No scenario mutates state directly; it only emits ticks
which the engine's worker pool drains.
"""