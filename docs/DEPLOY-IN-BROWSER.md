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

## One-time — Stripe `invoice.paid` webhook (online payments → Monday Paid)

Online Stripe pay (customer clicks the hosted-invoice "Pay Now" link) needs
a webhook pointed at the portal so the Invoices Sent board flips to Paid
automatically. Get the Cloud Run URL first (Cloud Run console → the
`gvc-invoice` service → URL at the top), then paste this into **Cloud
Shell** (same place as the deploy block above):

```bash
set -euo pipefail
PROJECT=gvc-invoice-system
REGION=us-central1
SERVICE=gvc-invoice
RUN_URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)')

# 1) Create the Stripe webhook endpoint for invoice.paid only (requires the
#    Stripe CLI to be logged in — `stripe login` once if this fails).
stripe webhook_endpoints create \
  --url "$RUN_URL/v1/webhooks/stripe" \
  --enabled-events invoice.paid

# 2) Paste the "Signing secret" (whsec_...) printed above:
read -p "Stripe signing secret (whsec_...): " WHSEC

# 3) Set it on Cloud Run — existing env vars/secrets are untouched.
gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT" \
  --update-env-vars STRIPE_WEBHOOK_SECRET="$WHSEC"

echo "DONE. Send a test invoice.paid event from the Stripe dashboard's webhook page to confirm."
```

Confirm without guessing: `GET https://portal.greenvalleycontractors.com/health` →
`stripe_webhook_secret_present: true`. (Presence only — still send a Stripe
test `invoice.paid` to prove Monday flips.)

No Stripe CLI in Cloud Shell? Create the endpoint by hand instead:
Stripe Dashboard → Developers → Webhooks → **Add endpoint** → URL
`<RUN_URL>/v1/webhooks/stripe` → event `invoice.paid` → copy the signing
secret it shows you, then just run step 3 (`gcloud run services update ...
--update-env-vars STRIPE_WEBHOOK_SECRET=whsec_...`) with that value.

---

## Forever — one button on GitHub (optional)

After the workflow in `.github/workflows/deploy-cloud-run.yml` is merged and
the `GCP_SA_KEY` secret is set (one-time; see that file’s header comment):

1. Open https://github.com/jordangvc/gvc-portal/actions/workflows/deploy-cloud-run.yml  
2. Click **Run workflow** → branch `master` → **Run workflow**.

No laptop terminal. No remembering flags.
