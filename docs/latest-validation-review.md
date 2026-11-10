# Latest commit validation and optimization review

Baseline: `5baa41d7bac00032ec8fd723400fd9a1e1f34788`.

This review preserves all files under `web/app/static/`, including the manual/RFID buttons newly added in the baseline. It does not revert those upstream UI changes or change the 17-stage workflow.

Confirmed and repaired:

- Fresh state directories could prevent the embedded RFID database from opening. Initialization now creates parent directories during application startup, and importing the standalone RFID API does not create a database.
- Reader failure silently substituted the first product tag and enqueued work. Automatic scans now return 503 without creating a task; explicit preset buttons remain available.
- Duplicate-tag scans could return the FIFO head for another product. They now return the matching task snapshot captured in the enqueue transaction.
- Concurrent requests could contend for the same PC/SC reader. One reader operation per process is accepted at a time; other requests return 409, while asynchronous API reads remain responsive.
- Listing tags performed five separate DB connections and omitted custom registrations. One query returns all registered products, preserving insertion order.
- Packaged wheels omitted the runtime `httpx` dependency and all default configuration assets. Wheels now carry an explicit allowlist of default assets and use a writable state directory outside site-packages.
- Web/controller default RFID paths were inconsistent. The lab launcher shares `RFID_DB`; the controller places it beside its journal, and the default web entrypoint uses the repository state directory.
- The old E2E runner referenced another checkout and an absolute Windows artifact path. Portable tests now exercise the validated `vision_picking` module.
- The bundled web tests omitted their rosbridge source dependencies. Those files were restored from the existing pinned upstream revision, with provenance recorded.
- Existing scenario fixtures lacked newly serialized nullable `target_joints` fields. Only fixture metadata was synchronized; motion steps and production scenario code were preserved. A telemetry test now uses a deterministic clock.

The review branch adds Ubuntu 24.04 Python/C++ and ROS 2 Jazzy build jobs. These jobs never run a robot bringup or connect to hardware. Local Windows checks and CI results are reported separately; passing MOCK tests does not certify real hardware or camera calibration.

Detailed final results and artifacts will be added after CI completes.
