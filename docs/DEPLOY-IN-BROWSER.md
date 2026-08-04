# Deploy the portal — browser only (no Mac gcloud)

You do **not** need gcloud installed on your Mac. Use **Google Cloud Shell**
(a terminal in the browser, already authenticated when you sign in as hello@).

## Today — Cloud Shell (about 2 minutes of clicking)

1. Open this link while signed in as **hello@greenvalleycontractors.com**:  
   https://console.cloud.google.com/cloudshell/editor?project=gvc-invoice-system&cloudshell=true

2. If Cloud Shell asks to authorize / start, click **Authorize** / **Continue**.

3. Paste this **entire** block into the Cloud Shell terminal and press Enter:

```bash
set -euo pipefail
PROJECT=gvc-invoice-system
REGION=us-central1
SERVICE=gvc-invoice
REPO=https://github.com/jordangvc/gvc-portal.git
DIR="$HOME/gvc-portal-deploy"

rm -rf "$DIR"
git clone --depth 1 --branch master "$REPO" "$DIR"
cd "$DIR"
gcloud config set project "$PROJECT"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT"
echo "DONE. Check hub footer at https://portal.greenvalleycontractors.com"
```

4. Wait until it prints `DONE`. Then open the portal and confirm the hub footer
   version moved (master is currently **r24+**).

That is the full deploy. Existing Cloud Run env vars and secrets stay as they are.

### If clone fails (private repo)

Cloud Shell needs GitHub access once:

```bash
gh auth login
# follow prompts → GitHub.com → HTTPS → login in browser
```

Then re-run the paste block above.

---

## Forever — one button on GitHub (optional)

After the workflow in `.github/workflows/deploy-cloud-run.yml` is merged and
the `GCP_SA_KEY` secret is set (one-time; see that file’s header comment):

1. Open https://github.com/jordangvc/gvc-portal/actions/workflows/deploy-cloud-run.yml  
2. Click **Run workflow** → branch `master` → **Run workflow**.

No laptop terminal. No remembering flags.
