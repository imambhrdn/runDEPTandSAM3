# Video queue

The Vast studio at `/` accepts up to eight videos per batch. Each video retains the
existing 30-second and upload-size limits. Choose a subject on the first frame of
every video, then select **Proses antrean · 1 GPU**.

The controller validates the entire batch before queueing it. Only the first job
owns the rental. A token-authenticated worker requests each next job in order;
repeated handoff requests return the same job. Job states and handoffs persist in
SQLite across controller restarts. Each video runs in a fresh subprocess on the
same instance, reusing installed dependencies and model downloads. A failed video
can be skipped; a runner failure, cancellation, or lease deadline stops the batch.

`MAX_RENT_MINUTES` applies to the **whole batch**, including setup. Its configured
value is displayed beside the uploader. `AUTO_DESTROY=0` retains the instance after
the batch: delete it manually in Vast.ai, where billing continues until deletion.
With `AUTO_DESTROY=1`, cleanup starts only after the last video or a session abort.
Another batch cannot start while the previous rental awaits cleanup.

Publish the worker commit before updating `WORKER_REF`; the worker must support
`WORKER_BATCH=1` and `/worker/{owner}/next`. Modal and HF keep their existing worker
protocol. Tests use fake rentals; deploying this change does not rent a GPU.
