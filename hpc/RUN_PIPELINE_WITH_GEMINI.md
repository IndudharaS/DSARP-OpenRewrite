# Run the HPC pipeline with Gemini recipe resolution

Gemini is optional. DistilBERT still generates the predictions; Gemini is used
only when deterministic repository analysis cannot turn a prediction into an
exact Move Class or Move Method candidate. Every proposed change must pass the
same deterministic source, API, diff, compilation, and test gates.

## 1. Store the key in the project

Run these commands on the HPC login node. The silent prompt prevents the key
from being printed or saved in shell history.

```bash
cd /scratch/hpc-prf-dssecs/$USER/dsarp-openrewrite
mkdir -p .secrets
chmod 700 .secrets
read -s -p "Gemini API key: " GEMINI_API_KEY; echo
printf '%s' "$GEMINI_API_KEY" > .secrets/gemini-api-key
chmod 600 .secrets/gemini-api-key
unset GEMINI_API_KEY
```

The `.secrets/` directory is ignored by Git. Do not paste the key into the web
form, Slurm command, source code, logs, or a committed configuration file.

Confirm only the permissions and byte count, not the key value:

```bash
stat -c '%a %n' .secrets/gemini-api-key
wc -c < .secrets/gemini-api-key
```

The permission output must be `600` (or stricter), and the byte count must be
greater than zero.

## 2. Restart the dashboard after updating code

```bash
hpc/manage_dashboard.sh restart
hpc/manage_dashboard.sh status
```

Reconnect the SSH port forward if needed and open `http://127.0.0.1:8765`.

## 3. Select Gemini in the web interface

Create a new experiment and choose **Google Gemini API** under **Recipe
resolution method**. The page should report that the secure key is ready.
Leave the model at `gemini-3.8-flash` initially, select a small validation batch,
and start the HPC experiment.

The compute node requires outbound HTTPS access to
`generativelanguage.googleapis.com`. If the preflight health check fails, use
the normal deterministic flow or ask the HPC administrators whether outbound
API access is permitted from compute nodes.

## Other modes

- **Normal deterministic flow** uses no LLM and needs no key.
- **Local LLM on HPC** uses the separately scheduled vLLM service and needs no
  Gemini key.

The Gemini API receives the bounded structural evidence needed to resolve a
candidate, including repository identifiers such as package, class, and method
names. Obtain the required project/privacy approval before using it on source
code that must not leave the cluster.
