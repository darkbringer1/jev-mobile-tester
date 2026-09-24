# First public demo: iOS simulator

1. Use a demo app with synthetic data and stable accessibility identifiers.
2. Record the simulator, CLI decisions, and elapsed time in one continuous take.
3. Supply one goal and independent final-screen assertions.
4. Repeat the task at least five times, including failed runs in the results.
5. Replay the generated YAML to show that the run leaves behind a useful test.

Measure app launch/session startup separately from the agent loop. The loop timer includes
all observations, Jev requests, freshness checks, actions, waits, and final assertions.
Record model version, Maestro version, simulator model/OS, sample count, failures, and
whether the app began in an identical state. Do not advertise browser-demo timings as
mobile timings. Compare against another agent only on the same task and starting state.

Launch deliverables: a short video, runnable instructions, an architecture diagram,
recorded results, known limits, and visible credit to upstream projects.
