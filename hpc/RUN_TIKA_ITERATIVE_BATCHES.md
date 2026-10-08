# Run Tika OpenRewrite batches iteratively

The first Tika run prepares the predictions, clone, matched baseline, generated
recipes, and a stable candidate-batch set. Later Slurm jobs reuse that same
logical run and execute only the selected OpenRewrite batch and downstream
verification stages.

## First run

In the web interface:

1. Select **Tika** and **Noctua HPC (Slurm)**.
2. Select **Start a new HPC run**.
3. Choose the desired complete or fast verified workflow.
4. Set **OpenRewrite candidates per batch** to `10`.
5. Set **Start from stored batch** to `1` and **Number of batches** to `1`.
6. Start the experiment.

After recipe generation, the run contains:

```text
results/prediction-batches/
├── predictions.csv
├── manifest.json
├── batch-0001-candidates.csv
├── batch-0001-predictions.csv
└── ...
```

`manifest.json` records the stable batch size, total batches, candidate counts,
prediction IDs, and a hash of the complete prediction CSV.

Each executed iteration is preserved separately under:

```text
results/openrewrite-batch-runs/batch-0001/attempt-SLURM_JOB_ID/
```

## Run the next batch

Create another experiment in the web interface:

1. Select the same Tika system and commit.
2. Select **Noctua HPC (Slurm)**.
3. Select **Run another stored OpenRewrite batch**.
4. Enter the original experiment's **Logical run** ID.
5. Keep **Resume from stage** set to **OpenRewrite**.
6. Keep the original batch size (`10`).
7. Set **Start from stored batch** to `2` and batches to `1`.
8. Start the experiment.

Repeat with batches 3, 4, and so on. The pipeline rejects a changed batch size
or a batch number beyond the stored manifest, preventing accidental remapping.
It reuses the original predictions, generated recipes, repository clone, and
matched baseline. It does not repeat mining, training, prediction, cloning, or
baseline Arcan measurement.

The equivalent direct Slurm command is:

```bash
cd /scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
sbatch --export=ALL,RESUME_RUN_ID=ORIGINAL_JOB_ID,START_STAGE=rewrite,\
START_BATCH=2,BATCH_SIZE=10,MAX_BATCHES=1 \
  hpc/noctua_pipeline.sbatch
```

Use the original logical run ID, not the new continuation job ID.
